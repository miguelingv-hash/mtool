#!/usr/bin/env python3
"""Carga de facturas SII a la BD desde un CSV Newman, directamente desde la
línea de comandos (sin pasar por HTTP / Caddy / parser HTTP).

Pensado para uploads masivos (>100 MB, >800 k filas) donde el flujo HTTP da
problemas de RAM, timeout o body limits. Reutiliza el mismo parser que el
endpoint `/sii/conciliar-newman/importar-faltantes-async`
(`_parsear_csv_newman`) → mismas garantías de formato y semántica.

Uso típico (dentro del contenedor backend):

    docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production \\
        exec -T backend python -m scripts.import_newman_sii \\
            --csv /data/facturas_TEC_Junio.csv \\
            --nif-titular A95000295 \\
            --nombre "TotalEnergies Clientes S.A.U." \\
            --ejercicio 2026 --periodo 06

Flags:
    --csv PATH                 (obligatorio) ruta al CSV en el contenedor
    --nif-titular STR          (obligatorio)
    --nombre STR               nombre social (default: "")
    --ejercicio STR            si lo informas, se rellena en todas las filas
                               que no lo traigan inferido del CSV
    --periodo STR              ídem ejercicio
    --only-faltantes           consulta BD antes y SOLO inserta facturas
                               cuya num_serie_factura no esté ya en
                               facturas_sii. Más lento pero coherente con
                               el modo "Importar faltantes" del UI.
    --batch-size N             tamaño del bulk_write (default 1000)
    --dry-run                  parsea y reporta SIN escribir en Mongo
    --keep-csv                 NO borra el CSV de origen tras carga OK

Exit codes:
    0   OK
    1   errores de parsing o validación de argumentos
    2   error de conexión/config (Mongo, env vars)
    130 interrumpido (Ctrl+C)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Asegura que el módulo padre (backend/) está en el path al ejecutarse vía
# `python -m scripts.import_newman_sii` desde otra carpeta.
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from scripts._common import (
    bulk_upsert,
    cleanup_csv,
    exclusive_lock,
    get_mongo_db,
    run_async,
    setup_logger,
)


def parse_args():
    p = argparse.ArgumentParser(
        description="Importa un CSV Newman a la colección facturas_sii.",
    )
    p.add_argument("--csv", required=True, type=Path, help="Ruta al CSV Newman.")
    p.add_argument("--nif-titular", required=True, help="NIF del titular emisor.")
    p.add_argument(
        "--nombre", default="",
        help="Razón social del titular (default: vacío).",
    )
    p.add_argument(
        "--ejercicio", default=None,
        help="Si el CSV no trae ejercicio en alguna fila, se rellena con éste.",
    )
    p.add_argument(
        "--periodo", default=None,
        help="Idem ejercicio para el periodo (formato '01'-'12').",
    )
    p.add_argument(
        "--only-faltantes", action="store_true",
        help="Solo insertar las facturas cuya num_serie_factura no esté ya en BD.",
    )
    p.add_argument("--batch-size", type=int, default=1000, help="Tamaño bulk_write.")
    p.add_argument("--dry-run", action="store_true", help="No escribe nada en Mongo.")
    p.add_argument(
        "--keep-csv", action="store_true",
        help="No borrar el CSV de origen tras una carga exitosa.",
    )
    return p.parse_args()


async def main_async(args):
    log = setup_logger("import_newman_sii")

    # ------------------------------------------------------------------
    # 1. Validaciones iniciales
    # ------------------------------------------------------------------
    if not args.csv.exists():
        log.error("El CSV no existe: %s", args.csv)
        sys.exit(1)
    csv_size_mb = args.csv.stat().st_size / 1024 / 1024
    log.info(
        "Iniciando import Newman SII · csv=%s (%.1f MB) · nif=%s · "
        "ejercicio=%s · periodo=%s · only_faltantes=%s · dry_run=%s",
        args.csv, csv_size_mb, args.nif_titular,
        args.ejercicio, args.periodo, args.only_faltantes, args.dry_run,
    )

    # ------------------------------------------------------------------
    # 2. Parser Newman (reutiliza el del backend)
    # ------------------------------------------------------------------
    from router_facturas import _parsear_csv_newman, init  # noqa: E402
    from imports_log import (  # noqa: E402
        add_import_errors,
        finish_import,
        start_import,
    )

    db = get_mongo_db()
    init(db, log)  # inyecta _db en router_facturas para que parsers internos lo vean

    # Audit trail: sólo si NO es dry-run (no queremos ensuciar el log con runs
    # de prueba). Si es dry-run, `import_id` queda a None y todos los helpers
    # se convierten en no-ops.
    import_id = None
    if not args.dry_run:
        import_id = await start_import(
            db,
            origen="sii",
            fuente="cli_newman",
            file_name=str(args.csv),
            file_size_bytes=args.csv.stat().st_size,
            user_id=None,
            user_email="CLI",
            nif_titular=args.nif_titular,
            ejercicio=args.ejercicio,
            periodo=args.periodo,
            extra={"only_faltantes": bool(args.only_faltantes)},
        )

    try:
        log.info("Leyendo CSV en memoria…")
        with args.csv.open("rb") as f:
            contenido = f.read()

        log.info("Parseando CSV (~%.1f MB)…", csv_size_mb)
        filas, errores, debug = _parsear_csv_newman(
            contenido, args.nif_titular, args.nombre or "",
        )
        log.info(
            "Parseo OK · filas válidas=%d · errores=%d · debug=%s",
            len(filas), len(errores), debug,
        )
        if errores:
            log.warning("Primeros errores (máx 5):")
            for e in errores[:5]:
                log.warning("  %s", e)
            if import_id:
                await add_import_errors(db, import_id, errores)
        if not filas:
            log.error("El CSV no contiene filas válidas. Abortando.")
            if import_id:
                await finish_import(
                    db, import_id, status="error",
                    error_message="CSV sin filas válidas",
                )
            sys.exit(1)

        # Relleno de ejercicio/periodo si el usuario lo aporta y el parser no lo
        # pudo inferir de la fecha (algunos exports Newman no lo traen).
        if args.ejercicio:
            for f in filas:
                f.setdefault("ejercicio", args.ejercicio)
                if not f.get("ejercicio"):
                    f["ejercicio"] = args.ejercicio
        if args.periodo:
            for f in filas:
                f.setdefault("periodo", args.periodo)
                if not f.get("periodo"):
                    f["periodo"] = args.periodo

        # ------------------------------------------------------------------
        # 3. Filtro --only-faltantes (consulta BD antes)
        # ------------------------------------------------------------------
        if args.only_faltantes:
            nums = [f["num_serie_factura"] for f in filas if f.get("num_serie_factura")]
            log.info(
                "Modo --only-faltantes: consultando cuáles de las %d facturas "
                "ya existen en facturas_sii…",
                len(nums),
            )
            existentes = set()
            # Chunk de 5000 para no superar el límite de tamaño del operador $in.
            for i in range(0, len(nums), 5000):
                chunk = nums[i : i + 5000]
                cursor = db.facturas_sii.find(
                    {"num_serie_factura": {"$in": chunk}},
                    {"num_serie_factura": 1, "_id": 0},
                )
                async for d in cursor:
                    existentes.add(d["num_serie_factura"])
            antes = len(filas)
            filas = [f for f in filas if f["num_serie_factura"] not in existentes]
            log.info(
                "Filtradas %d → %d filas (descartadas %d ya presentes en BD).",
                antes, len(filas), antes - len(filas),
            )
            if not filas:
                log.info("Nada que importar — todas las facturas ya están en BD.")
                cleanup_csv(args.csv, not args.keep_csv, log)
                if import_id:
                    await finish_import(
                        db, import_id, status="done",
                        total_procesados=antes,
                        insertados=0,
                        actualizados=0,
                        extra={"ya_en_bd": len(existentes)},
                    )
                return

        # ------------------------------------------------------------------
        # 4. Dry-run: cuenta y sale sin escribir
        # ------------------------------------------------------------------
        if args.dry_run:
            log.info(
                "[DRY-RUN] Se habrían upserteado %d documentos en facturas_sii. "
                "No se ha tocado la BD.", len(filas),
            )
            return

        # ------------------------------------------------------------------
        # 5. Bulk upsert
        # ------------------------------------------------------------------
        log.info("Insertando %d documentos en facturas_sii…", len(filas))
        resumen = await bulk_upsert(
            db, "facturas_sii", filas,
            fuente="cli_newman_sii",
            log=log,
            batch_size=args.batch_size,
            import_id=import_id,
        )
        rate = resumen["procesadas"] / resumen["duracion_s"] if resumen["duracion_s"] > 0 else 0
        log.info(
            "✅ Carga completada · procesadas=%d · insertadas_nuevas=%d · "
            "actualizadas=%d · duración=%.1f s · %.0f docs/s",
            resumen["procesadas"], resumen["insertadas"],
            resumen["modificadas"], resumen["duracion_s"], rate,
        )

        if import_id:
            await finish_import(
                db, import_id, status="done",
                total_procesados=resumen["procesadas"],
                insertados=resumen["insertadas"],
                actualizados=resumen["modificadas"],
            )

        # ------------------------------------------------------------------
        # 6. Limpieza del CSV
        # ------------------------------------------------------------------
        cleanup_csv(args.csv, not args.keep_csv, log)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        if import_id:
            await finish_import(
                db, import_id, status="error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
        raise


def main():
    args = parse_args()
    try:
        with exclusive_lock("import_newman_sii.lock"):
            run_async(main_async(args))
    except RuntimeError as e:
        # Lock ya tomado por otra ejecución
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
