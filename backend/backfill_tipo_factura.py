"""Backfill / denormalización de `tipo_factura` desde `facturas_sii` a
`facturas_comercial`.

Motivación (iter25 · Feb 2026):
    Antes, todos los filtros por `tipo_factura` requerían un `$lookup`
    entre `facturas_comercial` (~1.5M docs) y `facturas_sii` (~1.5M
    docs) por `num_serie_factura`, con coste 30-60s en cache-miss. Al
    denormalizar el campo en el propio comercial, el filtro se aplica
    con `$match` directo → sub-segundo.

Estrategia:
    Aggregation pipeline con `$merge` nativo. Se ejecuta enteramente
    en el servidor Mongo, sin round-trips Python. Empareja por
    `num_serie_factura` (que tiene índice único en ambas colecciones).

Semántica:
    - Comercial con match SII → `tipo_factura` = valor del SII.
    - Comercial sin match SII → `tipo_factura` se deja como está
      (típicamente ausente/null → cuenta como `_sin_clasificar`).

Idempotente: se puede lanzar N veces; sólo actualiza cuando el valor
    difiere.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any


async def backfill_tipo_factura_comercial(
    db: Any,
    logger: logging.Logger | None = None,
    nif_titular: str | None = None,
) -> dict:
    """Propaga `tipo_factura` (y `ejercicio`, `periodo` del SII como
    `tipo_factura_source`) desde SII a Comercial.

    Args:
        db: instancia motor.AsyncIOMotorDatabase
        logger: logger opcional para trazas
        nif_titular: si se aporta, backfill sólo para esa sociedad
            (útil para reintentos incrementales)

    Returns:
        dict con contadores {matched, updated, sin_match_previo}.
    """
    log = logger or logging.getLogger(__name__)
    import time
    t0 = time.monotonic()

    match_stage: dict = {}
    if nif_titular:
        match_stage["nif_titular"] = nif_titular.strip().upper()

    # Contadores pre-backfill (para el "reporte")
    total_com = await db.facturas_comercial.count_documents(match_stage)
    con_tipo_prev = await db.facturas_comercial.count_documents({
        **match_stage,
        "tipo_factura": {"$exists": True, "$nin": [None, ""]},
    })

    # Pipeline: SII → project campos → $merge en facturas_comercial.
    # `whenMatched: "merge"` copia los campos del source (SII) sobre el
    # destino (Comercial) preservando el resto. Usamos $project para
    # llevar SÓLO los campos que queremos denormalizar → nada más se toca.
    pipeline: list[dict] = []
    if match_stage:
        pipeline.append({"$match": match_stage})
    pipeline.extend([
        {"$project": {
            "_id": 0,
            "num_serie_factura": 1,
            "tipo_factura": 1,
        }},
        {"$match": {"tipo_factura": {"$ne": None}}},
        {"$merge": {
            "into": "facturas_comercial",
            "on": "num_serie_factura",
            "whenMatched": "merge",
            "whenNotMatched": "discard",
        }},
    ])

    log.info("[backfill] Arrancando $merge SII → Comercial (nif=%s)", nif_titular or "*")
    # El $merge no devuelve cursor, sólo ejecuta la escritura.
    await db.facturas_sii.aggregate(pipeline, allowDiskUse=True).to_list(length=1)
    dur = time.monotonic() - t0

    # Contadores post-backfill
    con_tipo_post = await db.facturas_comercial.count_documents({
        **match_stage,
        "tipo_factura": {"$exists": True, "$nin": [None, ""]},
    })
    sin_tipo_post = total_com - con_tipo_post

    delta = con_tipo_post - con_tipo_prev
    log.info(
        "[backfill] Completado en %.1fs. total=%d, con_tipo_pre=%d → post=%d "
        "(+%d), sin_tipo=%d (=solo_comercial sin match SII)",
        dur, total_com, con_tipo_prev, con_tipo_post, delta, sin_tipo_post,
    )

    return {
        "duracion_s": round(dur, 1),
        "total_comercial": total_com,
        "con_tipo_pre": con_tipo_prev,
        "con_tipo_post": con_tipo_post,
        "actualizados": delta,
        "sin_tipo_post": sin_tipo_post,
    }


async def ensure_indexes_iter25(db: Any, logger: logging.Logger | None = None) -> None:
    """Crea los índices compuestos que aprovechan el tipo_factura ahora
    denormalizado en facturas_comercial. Idempotente.
    """
    log = logger or logging.getLogger(__name__)
    # facturas_comercial: (nif_titular, ejercicio, periodo, tipo_factura)
    await db.facturas_comercial.create_index(
        [("nif_titular", 1), ("ejercicio", 1), ("periodo", 1), ("tipo_factura", 1)],
        name="nif_ejerc_per_tipo_com_idx",
        background=True,
    )
    # facturas_comercial: (nif_titular, tipo_factura) — para queries sin período
    await db.facturas_comercial.create_index(
        [("nif_titular", 1), ("tipo_factura", 1)],
        name="nif_tipo_com_idx",
        background=True,
    )
    # facturas_sii: (nif_titular, ejercicio, periodo, tipo_factura)
    await db.facturas_sii.create_index(
        [("nif_titular", 1), ("ejercicio", 1), ("periodo", 1), ("tipo_factura", 1)],
        name="nif_ejerc_per_tipo_sii_idx",
        background=True,
    )
    log.info("[backfill] Índices iter25 creados/verificados")


if __name__ == "__main__":
    """CLI: python -m backfill_tipo_factura [nif_titular]

    Ejecuta backfill de tipo_factura sobre la BD configurada en .env.
    Uso típico tras carga masiva SII o Comercial.
    """
    import os
    import sys
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv

    load_dotenv("/app/backend/.env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log = logging.getLogger("backfill_tipo_factura")

    async def _run():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        nif = sys.argv[1] if len(sys.argv) > 1 else None
        await ensure_indexes_iter25(db, log)
        report = await backfill_tipo_factura_comercial(db, log, nif_titular=nif)
        print("\nReporte final:")
        for k, v in report.items():
            print(f"  {k}: {v}")
        client.close()

    asyncio.run(_run())
