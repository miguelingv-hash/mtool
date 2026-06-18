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
    ap.add_argument(
        "--from-start",
        action="store_true",
        help="Ignora el state file de reanudación y arranca desde la página 1",
    )
    ap.add_argument(
        "--verificar-completitud",
        action="store_true",
        help="Comprueba si AEAT tiene facturas posteriores a las ya descargadas "
             "en BD para este (nif, ejercicio, periodo). Construye ClavePaginacion "
             "desde la última factura del periodo en BD (o desde el state file si "
             "existe) y consulta AEAT. Cualquier factura nueva se inserta. Útil "
             "cuando dudas si el periodo está completo.",
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

    # Fichero de estado para reanudar: junto al config, con sufijo `.state.json`.
    # Guarda la última ClavePaginacion procesada con éxito + contadores.
    import json
    state_path = Path(args.config).with_suffix(".state.json")
    state_key = f"{nif}|{ejercicio}|{periodo}|{entorno}"

    def cargar_estado_previo() -> dict:
        if args.from_start or not state_path.exists():
            return {}
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
        if data.get("key") != state_key:
            print(
                f"AVISO: state file existe pero corresponde a otra descarga "
                f"({data.get('key')!r} != {state_key!r}). Se ignora.",
                flush=True,
            )
            return {}
        return data

    def guardar_estado(clave_pag, pagina, acumuladas):
        try:
            state_path.write_text(
                json.dumps({
                    "key": state_key,
                    "clave_pag": clave_pag,
                    "pagina": pagina,
                    "acumuladas": acumuladas,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            print("AVISO: no se pudo escribir el state file de reanudación", flush=True)

    estado_previo = cargar_estado_previo()
    start_clave = estado_previo.get("clave_pag")
    start_pagina = estado_previo.get("pagina", 0)
    start_invoices = estado_previo.get("acumuladas", 0)
    if start_clave:
        print(
            f"INFO Reanudando desde página {start_pagina + 1} "
            f"({start_invoices:,} facturas ya descargadas en ejecuciones previas).",
            flush=True,
        )

    # --- Modo verificación de completitud ---------------------------------
    # Calcula la ClavePaginacion a partir de la última factura del periodo en
    # BD (la mayor lexicográficamente por num_serie + fecha) si no hay state
    # file. AEAT sólo devolverá facturas que vengan DESPUÉS de esa clave en
    # su ordenación interna → si devuelve 0, el periodo está completo.
    verificar = args.verificar_completitud
    if verificar and not start_clave:
        ult = coll.find_one(
            {"ejercicio": ejercicio, "periodo": periodo, "nif_titular": nif},
            sort=[
                ("num_serie_factura", -1),
                ("fecha_expedicion", -1),
            ],
            projection={
                "_id": 0,
                "num_serie_factura": 1,
                "fecha_expedicion": 1,
            },
        )
        if ult:
            start_clave = {
                "IDEmisorFactura": {"NIF": nif},
                "NumSerieFacturaEmisor": ult["num_serie_factura"],
                "FechaExpedicionFacturaEmisor": ult["fecha_expedicion"],
            }
            # Para que la auditoría de pagina sea coherente, partimos de la
            # cuenta actual en BD para este (nif, ejercicio, periodo).
            start_invoices = coll.count_documents({
                "ejercicio": ejercicio,
                "periodo": periodo,
                "nif_titular": nif,
            })
            start_pagina = 0
            print(
                f"INFO Verificación: ClavePaginacion construida desde la "
                f"última factura del periodo en BD: "
                f"{ult['num_serie_factura']!r} / {ult['fecha_expedicion']!r} "
                f"({start_invoices:,} facturas previas en BD).",
                flush=True,
            )
        else:
            print(
                f"AVISO Verificación: no hay facturas en BD para "
                f"({nif}, {ejercicio}, {periodo}). Se hará una descarga "
                f"completa desde el principio.",
                flush=True,
            )

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
                # Guardamos estado de reanudación tras cada bulk_write exitoso
                guardar_estado(clave_pag, pagina, acumuladas)
                print(
                    f"[pág {pagina:>3}] +{len(ops):>5} facturas | "
                    f"total persistidas: {persistidas_total[0]:>7,} | "
                    f"bulk {time.time()-t0:.1f}s | "
                    f"tiempo total: {elapsed/60:.1f}min",
                    flush=True,
                )
        return False  # nunca cancelamos desde CLI

    print("=" * 70)
    if verificar:
        print("  Verificación de completitud SII — CLI")
    else:
        print("  Descarga masiva de facturas SII — CLI")
    print("=" * 70)
    print(f"  NIF titular  : {nif}")
    print(f"  Razón social : {razon}")
    print(f"  Periodo      : {ejercicio}-{periodo}")
    print(f"  Entorno      : {entorno}")
    print(f"  Mongo        : {db_name}@{mongo_url.split('@')[-1].split('/')[0]}")
    print(f"  facturas_sii : {docs_antes:,} docs (antes)")
    print(f"  Máx páginas  : {args.max_paginas or 'todas'}")
    if verificar:
        print("  Modo         : --verificar-completitud (sólo descarga lo nuevo)")
    if start_clave:
        print(f"  Reanudando   : desde página {start_pagina + 1} "
              f"({start_invoices:,} facturas previas)")
    print("=" * 70, flush=True)

    try:
        facturas, _req_xml, _resp_xml = rf._consultar_mensual_real(
            client, nif, razon, ejercicio, periodo, entorno,
            progress_cb=progress_cb,
            max_paginas=args.max_paginas,
            start_clave=start_clave,
            start_pagina=start_pagina,
            start_invoices=start_invoices,
        )
    except KeyboardInterrupt:
        print("\n*** Interrupción manual (Ctrl-C). Las facturas ya descargadas "
              "quedan persistidas en `facturas_sii`. Relanza el mismo comando "
              "para reanudar desde donde se quedó. ***", flush=True)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\n*** ERROR durante la descarga: {exc} ***", flush=True)
        print(f"*** Las {persistidas_total[0]:,} facturas descargadas hasta la "
              f"página {paginas[0]} están persistidas en `facturas_sii`.", flush=True)
        if state_path.exists():
            print(f"*** Para REANUDAR desde donde se quedó, vuelve a lanzar:\n"
                  f"      python scripts/descargar_sii.py --config {args.config}\n"
                  f"    (state guardado en {state_path.name})", flush=True)
        raise

    # Descarga completada con éxito → eliminamos el state file
    try:
        if state_path.exists():
            state_path.unlink()
    except Exception:  # noqa: BLE001
        pass

    elapsed = time.time() - t_start
    docs_despues = coll.count_documents({})
    print("=" * 70)
    if verificar:
        # En modo verificación, lo importante es saber si el periodo está
        # completo o no. `facturas` aquí incluye las que vienen DESPUÉS de la
        # ClavePaginacion construida desde BD.
        if len(facturas) == 0:
            print("  VERIFICACIÓN: PERIODO COMPLETO ✓")
            print("=" * 70)
            print("  AEAT no devolvió ninguna factura adicional tras la última")
            print(f"  factura que tenías en BD para {nif} / {ejercicio}-{periodo}.")
            print("  La descarga local está al día.")
        else:
            print("  VERIFICACIÓN: FALTABAN FACTURAS ⚠")
            print("=" * 70)
            print(f"  AEAT devolvió {len(facturas):,} facturas posteriores a las")
            print("  ya almacenadas. Se han persistido en `facturas_sii`.")
        print(f"  Persistidas en esta verificación: {persistidas_total[0]:,}")
    else:
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
