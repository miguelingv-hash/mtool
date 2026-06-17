#!/usr/bin/env python3
"""Descarga masiva de facturas del SII vía CLI.

Lanza una consulta mensual paginada completa al SII de la AEAT, persistiendo
las facturas en MongoDB página a página. Pensado para ejecutarse fuera del
backend web (sin timeouts de Cloudflare/ingress) directamente en tu equipo
o un servidor con acceso al certificado.

Uso:
    python descargar_sii.py --config descarga.txt
    python descargar_sii.py --config descarga.txt --max-paginas 5

Formato del fichero de configuración (texto plano, key=value por línea):

    # Líneas que empiezan por # se ignoran
    cert_path     = /ruta/al/certificado.pfx
    cert_password = miPasswordDelPfx
    nif           = A95000295
    razon_social  = TotalEnergies Clientes S.A.U.
    ejercicio     = 2026
    periodo       = 05
    entorno       = produccion          # produccion | preproduccion | produccion_sello | preproduccion_sello
    # mongo_url y db_name son opcionales: si faltan se cogen del backend/.env
    mongo_url     = mongodb://localhost:27017
    db_name       = sii_db
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Añade /app/backend al sys.path para poder importar sii_client y router_facturas
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def cargar_config(path: str) -> dict:
    """Lee un fichero .txt de configuración con líneas `clave = valor`."""
    cfg: dict = {}
    p = Path(path)
    if not p.exists():
        sys.exit(f"ERROR: No existe el fichero de configuración: {path}")
    for n, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            sys.exit(f"ERROR: Línea {n} sin '=': {raw!r}")
        k, v = line.split("=", 1)
        cfg[k.strip().lower()] = v.strip().strip('"').strip("'")
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Descarga masiva de facturas SII")
    ap.add_argument("--config", required=True, help="Fichero .txt con parámetros")
    ap.add_argument(
        "--max-paginas",
        type=int,
        default=None,
        help="Límite de páginas a descargar (None = todas)",
    )
    args = ap.parse_args()

    cfg = cargar_config(args.config)
    requeridos = ["cert_path", "cert_password", "nif", "razon_social",
                  "ejercicio", "periodo"]
    faltan = [k for k in requeridos if k not in cfg]
    if faltan:
        sys.exit(f"ERROR: Faltan claves en {args.config}: {', '.join(faltan)}")

    # Carga .env del backend para MONGO_URL/DB_NAME si no están en el config
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_DIR / ".env")
    except ImportError:
        pass

    mongo_url = cfg.get("mongo_url") or os.environ.get("MONGO_URL")
    db_name = cfg.get("db_name") or os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        sys.exit(
            "ERROR: Falta MONGO_URL/DB_NAME (en config o en variables de entorno). "
            "Añádelos en el fichero .txt o exporta las variables."
        )

    cert_path = Path(cfg["cert_path"]).expanduser()
    if not cert_path.exists():
        sys.exit(f"ERROR: Certificado no encontrado: {cert_path}")
    cert_bytes = cert_path.read_bytes()
    cert_pwd = cfg["cert_password"]
    nif = cfg["nif"]
    razon = cfg["razon_social"]
    ejercicio = cfg["ejercicio"]
    periodo = cfg["periodo"].zfill(2)
    entorno = cfg.get("entorno", "produccion").lower()

    # Conexión a Mongo (síncrona)
    from pymongo import MongoClient, UpdateOne

    mongo = MongoClient(mongo_url)
    coll = mongo[db_name]["facturas_sii"]
    coll.create_index("num_serie_factura", unique=True)
    docs_antes = coll.count_documents({})

    # Cliente SII real con certificado
    from sii_client import build_client
    client = build_client("real", cert_bytes=cert_bytes, cert_password=cert_pwd)

    # Inicializa el logger global del router_facturas (necesario para que
    # `_consultar_mensual_real` no falle al llamar a `_logger.info(...)`).
    import logging
    import router_facturas as rf
    log = logging.getLogger("sii_cli")
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    rf.init(db=None, logger=log)

    paginas = [0]
    persistidas_total = [0]
    t_start = time.time()

    def progress_cb(pagina, acumuladas, clave_pag, facturas_pagina):
        """Callback síncrono: hace bulk_write a Mongo y muestra progreso."""
        if facturas_pagina:
            now = datetime.now(timezone.utc).isoformat()
            ops = []
            for f in facturas_pagina:
                if not f.get("num_serie_factura"):
                    continue
                ops.append(
                    UpdateOne(
                        {"num_serie_factura": f["num_serie_factura"]},
                        {"$set": {**f,
                                  "fuente_ultima": "cli_consulta_mensual",
                                  "ultima_actualizacion": now}},
                        upsert=True,
                    )
                )
            if ops:
                t0 = time.time()
                coll.bulk_write(ops, ordered=False)
                paginas[0] += 1
                persistidas_total[0] += len(ops)
                elapsed = time.time() - t_start
                print(
                    f"[pág {pagina:>3}] +{len(ops):>5} facturas | "
                    f"total persistidas: {persistidas_total[0]:>7,} | "
                    f"bulk {time.time()-t0:.1f}s | "
                    f"tiempo total: {elapsed/60:.1f}min",
                    flush=True,
                )
        return False  # nunca cancelamos desde CLI

    print("=" * 70)
    print("  Descarga masiva de facturas SII — CLI")
    print("=" * 70)
    print(f"  NIF titular  : {nif}")
    print(f"  Razón social : {razon}")
    print(f"  Periodo      : {ejercicio}-{periodo}")
    print(f"  Entorno      : {entorno}")
    print(f"  Mongo        : {db_name}@{mongo_url.split('@')[-1].split('/')[0]}")
    print(f"  facturas_sii : {docs_antes:,} docs (antes)")
    print(f"  Máx páginas  : {args.max_paginas or 'todas'}")
    print("=" * 70, flush=True)

    try:
        facturas, _req_xml, _resp_xml = rf._consultar_mensual_real(
            client, nif, razon, ejercicio, periodo, entorno,
            progress_cb=progress_cb,
            max_paginas=args.max_paginas,
        )
    except KeyboardInterrupt:
        print("\n*** Interrupción manual (Ctrl-C). Las facturas ya descargadas "
              "quedan persistidas en `facturas_sii`. ***", flush=True)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\n*** ERROR durante la descarga: {exc} ***", flush=True)
        raise

    elapsed = time.time() - t_start
    docs_despues = coll.count_documents({})
    print("=" * 70)
    print("  DESCARGA COMPLETADA")
    print("=" * 70)
    print(f"  Facturas devueltas por el SII : {len(facturas):,}")
    print(f"  Persistidas en esta ejecución : {persistidas_total[0]:,}")
    print(f"  facturas_sii (antes / después): {docs_antes:,} / {docs_despues:,}")
    print(f"  Páginas procesadas            : {paginas[0]}")
    print(f"  Tiempo total                  : {elapsed/60:.1f} min "
          f"({elapsed:.0f} s)")
    print("=" * 70)


if __name__ == "__main__":
    main()
