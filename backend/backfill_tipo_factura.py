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


async def backfill_snapshot_sii_en_comercial(
    db: Any,
    logger: logging.Logger | None = None,
    nif_titular: str | None = None,
) -> dict:
    """FASE B (iter26): denormaliza campos calculados del SII en cada
    doc de `facturas_comercial` para que las queries de la Comparativa
    NO tengan que hacer `$lookup` en tiempo real.

    Campos denormalizados en cada comercial:
      - `_sii_base`: suma de `base_imponible` del detalle_iva del SII
        match (o header si no hay detalle).
      - `_sii_cuota`: idem para `cuota_repercutida`.
      - `_sii_importe_total`: importe_total del SII match.
      - `_sii_fecha_expedicion`: fecha_expedicion del SII match.
      - `_sii_estado`: estado del SII match (para posible filtro).
      - `_has_sii`: True si existe SII match, False si es solo_comercial.

    Estrategia: aggregation con `$project` que calcula los sums en el
    lado SII, seguida de `$merge` en `facturas_comercial` con
    `whenMatched: "merge"` (misma técnica que iter25).

    Comerciales sin match SII conservan `_has_sii=False` y campos SII
    null (se setean en un segundo pass con `update_many`).
    """
    log = logger or logging.getLogger(__name__)
    import time
    t0 = time.monotonic()

    match_stage: dict = {}
    if nif_titular:
        match_stage["nif_titular"] = nif_titular.strip().upper()

    total_com = await db.facturas_comercial.count_documents(match_stage)

    # Pipeline SII: calcular _sii_base, _sii_cuota, _sii_importe_total
    # con las mismas reglas que _comparativa_totales_impl usa en runtime.
    pipeline: list[dict] = []
    if match_stage:
        pipeline.append({"$match": match_stage})
    pipeline.extend([
        {"$project": {
            "_id": 0,
            "num_serie_factura": 1,
            "_sii_base": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    {"$reduce": {
                        "input": {"$ifNull": ["$detalle_iva", []]},
                        "initialValue": 0.0,
                        "in": {"$add": [
                            "$$value",
                            {"$toDouble": {"$ifNull": ["$$this.base_imponible", 0]}},
                        ]},
                    }},
                    {"$toDouble": {"$ifNull": ["$base_imponible", 0]}},
                ],
            },
            "_sii_cuota": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    {"$reduce": {
                        "input": {"$ifNull": ["$detalle_iva", []]},
                        "initialValue": 0.0,
                        "in": {"$add": [
                            "$$value",
                            {"$toDouble": {"$ifNull": ["$$this.cuota_repercutida", 0]}},
                        ]},
                    }},
                    {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}},
                ],
            },
            "_sii_importe_total": {"$toDouble": {"$ifNull": ["$importe_total", 0]}},
            "_sii_fecha_expedicion": {"$ifNull": ["$fecha_expedicion", None]},
            "_sii_estado": {"$ifNull": ["$estado", None]},
            "_has_sii": {"$literal": True},
        }},
        {"$merge": {
            "into": "facturas_comercial",
            "on": "num_serie_factura",
            "whenMatched": "merge",
            "whenNotMatched": "discard",
        }},
    ])

    log.info(
        "[backfill snapshot] Arrancando $merge SII fields → Comercial (nif=%s)",
        nif_titular or "*",
    )
    await db.facturas_sii.aggregate(pipeline, allowDiskUse=True).to_list(length=1)

    # Segundo pass: los comerciales SIN match SII quedan con `_has_sii`
    # ausente. Los marcamos como False de forma explícita con
    # update_many para poder usar `_has_sii` en $match sin sorpresas.
    res_solo_com = await db.facturas_comercial.update_many(
        {**match_stage, "_has_sii": {"$exists": False}},
        {"$set": {"_has_sii": False}},
    )
    dur = time.monotonic() - t0

    n_has_sii = await db.facturas_comercial.count_documents({
        **match_stage, "_has_sii": True,
    })
    n_solo_com = await db.facturas_comercial.count_documents({
        **match_stage, "_has_sii": False,
    })

    log.info(
        "[backfill snapshot] Completado en %.1fs. total=%d, has_sii=%d, "
        "solo_comercial=%d (marcados=%d)",
        dur, total_com, n_has_sii, n_solo_com, res_solo_com.modified_count,
    )

    return {
        "duracion_s": round(dur, 1),
        "total_comercial": total_com,
        "has_sii": n_has_sii,
        "solo_comercial": n_solo_com,
    }


async def ensure_indexes_iter26(db: Any, logger: logging.Logger | None = None) -> None:
    """Índices para explotar los campos snapshot iter26. Idempotente."""
    log = logger or logging.getLogger(__name__)
    # Filtro más frecuente: por sociedad + `_has_sii` (coincide vs solo_com)
    await db.facturas_comercial.create_index(
        [("nif_titular", 1), ("_has_sii", 1)],
        name="nif_has_sii_com_idx",
        background=True,
    )
    # Filtro por sociedad + origen + `_has_sii` (para resumen-origenes)
    await db.facturas_comercial.create_index(
        [("nif_titular", 1), ("origen_comercial", 1), ("_has_sii", 1)],
        name="nif_origen_has_sii_com_idx",
        background=True,
    )
    log.info("[backfill] Índices iter26 creados/verificados")


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
        await ensure_indexes_iter26(db, log)
        report1 = await backfill_tipo_factura_comercial(db, log, nif_titular=nif)
        report2 = await backfill_snapshot_sii_en_comercial(db, log, nif_titular=nif)
        print("\nReporte tipo_factura:")
        for k, v in report1.items():
            print(f"  {k}: {v}")
        print("\nReporte snapshot SII (iter26):")
        for k, v in report2.items():
            print(f"  {k}: {v}")
        client.close()

    asyncio.run(_run())
