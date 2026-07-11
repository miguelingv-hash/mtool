#!/usr/bin/env python3
"""Carga de facturas comerciales (SAP FI / SIGLO) a la BD desde un report
tabular, directamente desde la línea de comandos.

Reutiliza el mismo parser que el endpoint `/comercial/csv`
(`_parsear_report_tabular`), incluyendo el mapeo `Soc.` → `nif_titular` +
`nombre_titular` desde el catálogo `sociedades_catalogo`.

Uso típico (dentro del contenedor backend):

    docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production \\
        exec -T backend python -m scripts.import_comercial \\
            --csv /data/sap_junio.txt

Flags:
    --csv PATH               (obligatorio) ruta al fichero (.txt o .csv)
    --batch-size N           tamaño bulk_write (default 1000)
    --dry-run                parsea y reporta SIN escribir
    --keep-csv               NO borra el CSV de origen tras carga OK
    --soc-override SOC       fuerza un Soc concreto para todas las filas que
                             vengan SIN Soc en el CSV (útil si tu export no la
                             trae bien). El Soc debe estar en el catálogo.

Exit codes igual que `import_newman_sii.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
        description="Importa un CSV/TXT SAP FI o SIGLO a facturas_comercial.",
    )
    p.add_argument("--csv", required=True, type=Path)
    p.add_argument(
        "--soc-override", default=None,
        help="Forzar Soc para filas que no lo traigan en el CSV. Debe estar en "
        "el catálogo (4432, 2239, ...).",
    )
    p.add_argument(
        "--nif-titular", default=None,
        help="Forzar directamente el NIF titular para TODAS las filas, "
        "ignorando la columna Soc. del CSV. Útil para reports SIGLO HC30 "
        "donde Soc. no es el código de sociedad. El NIF debe estar en el "
        "catálogo de sociedades.",
    )
    p.add_argument("--batch-size", type=int, default=1000)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--keep-csv", action="store_true",
        help="No borrar el CSV de origen tras una carga exitosa.",
    )
    return p.parse_args()


async def main_async(args):
    log = setup_logger("import_comercial")

    if not args.csv.exists():
        log.error("El CSV no existe: %s", args.csv)
        sys.exit(1)
    csv_size_mb = args.csv.stat().st_size / 1024 / 1024
    log.info(
        "Iniciando import comercial · csv=%s (%.1f MB) · soc_override=%s · "
        "dry_run=%s",
        args.csv, csv_size_mb, args.soc_override, args.dry_run,
    )

    # Imports diferidos para no acoplar test-time
    from router_facturas import (  # noqa: E402
        _cargar_catalogo_sociedades,
        _detectar_formato_tabular,
        _parsear_csv_generico,
        _parsear_report_tabular,
        init,
    )

    db = get_mongo_db()
    init(db, log)

    log.info("Leyendo CSV…")
    raw = args.csv.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # SAP suele exportar en latin-1
        text = raw.decode("latin-1")
        log.info("CSV decodificado como latin-1 (no era UTF-8).")

    origen = _detectar_formato_tabular(text)
    if origen:
        log.info("Formato detectado: %s", origen)
        catalogo = await _cargar_catalogo_sociedades()
        log.info(
            "Catálogo de sociedades cargado · %d entradas: %s",
            len(catalogo), list(catalogo.keys()),
        )
        registros, errores = _parsear_report_tabular(
            text, origen, catalogo_sociedades=catalogo,
        )
    else:
        log.info(
            "No se detectó SAP FI ni SIGLO por la cabecera; "
            "intentando parser CSV genérico.",
        )
        registros, errores = _parsear_csv_generico(text)
        # En genérico no hay catalogo applied → log de aviso
        log.warning(
            "Parser genérico: las facturas no llevarán nif_titular asignado. "
            "Usa el endpoint admin /api/admin/comercial/asignar-nif-titular-por-soc "
            "después si necesitas asignarlo.",
        )

    log.info("Parseo OK · filas válidas=%d · errores=%d", len(registros), len(errores))
    if errores:
        log.warning("Primeros errores (máx 5):")
        for e in errores[:5]:
            log.warning("  %s", e)
    if not registros:
        log.error("El CSV no contiene filas válidas. Abortando.")
        sys.exit(1)

    # --soc-override: rellena nif_titular para filas sin Soc/sin nif
    if args.soc_override and origen:
        soc = str(args.soc_override).strip()
        catalogo_local = await _cargar_catalogo_sociedades()
        mapping = catalogo_local.get(soc)
        if not mapping:
            log.error(
                "--soc-override=%s no está en el catálogo (%s). "
                "Añádelo con PUT /api/admin/sociedades.",
                soc, list(catalogo_local.keys()),
            )
            sys.exit(1)
        rellenadas = 0
        for r in registros:
            if not r.get("nif_titular"):
                r["nif_titular"] = mapping["nif_titular"]
                r["nombre_titular"] = mapping["nombre_titular"]
                r["soc_origen"] = soc
                rellenadas += 1
        log.info(
            "Aplicado --soc-override=%s a %d/%d registros sin nif_titular.",
            soc, rellenadas, len(registros),
        )

    # --nif-titular: fuerza directamente el NIF+nombre en TODAS las filas
    # (más simple que --soc-override cuando la columna Soc. no es fiable).
    if args.nif_titular:
        nif_norm = args.nif_titular.strip().upper()
        catalogo_local = await _cargar_catalogo_sociedades()
        mapping = None
        for _soc, info in catalogo_local.items():
            if info.get("nif_titular") == nif_norm:
                mapping = info
                break
        if not mapping:
            log.error(
                "--nif-titular=%s no está en el catálogo. NIFs válidos: %s",
                nif_norm,
                sorted({v["nif_titular"] for v in catalogo_local.values()}),
            )
            sys.exit(1)
        for r in registros:
            r["nif_titular"] = mapping["nif_titular"]
            r["nombre_titular"] = mapping.get("nombre_titular") or ""
        log.info(
            "Aplicado --nif-titular=%s (%s) a TODAS las %d filas.",
            nif_norm, mapping.get("nombre_titular") or "", len(registros),
        )

    if args.dry_run:
        log.info(
            "[DRY-RUN] Se habrían upserteado %d documentos en facturas_comercial.",
            len(registros),
        )
        return

    log.info("Insertando %d documentos en facturas_comercial…", len(registros))
    resumen = await bulk_upsert(
        db, "facturas_comercial", registros,
        fuente="cli_comercial",
        log=log,
        batch_size=args.batch_size,
    )
    rate = resumen["procesadas"] / resumen["duracion_s"] if resumen["duracion_s"] > 0 else 0
    log.info(
        "✅ Carga completada · procesadas=%d · insertadas_nuevas=%d · "
        "actualizadas=%d · duración=%.1f s · %.0f docs/s",
        resumen["procesadas"], resumen["insertadas"],
        resumen["modificadas"], resumen["duracion_s"], rate,
    )

    cleanup_csv(args.csv, not args.keep_csv, log)


def main():
    args = parse_args()
    try:
        with exclusive_lock("import_comercial.lock"):
            run_async(main_async(args))
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
