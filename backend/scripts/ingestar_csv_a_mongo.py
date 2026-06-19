"""
ingestar_csv_a_mongo.py
=======================

Ingiere un CSV de facturas emitidas (generado por la colección Postman/Newman
de esta misma carpeta, vía `extraer_csv.py`) directamente en la colección
`facturas_sii` de MongoDB usando `bulk_write` con `upsert` por
`num_serie_factura`.

Beneficios:
  - Independiza la descarga (rápida, vía Newman en local) del registro en BD.
  - 100% idempotente: relanzar con el mismo CSV deja la BD igual.
  - La Comparativa y la UI ven estas facturas exactamente igual que las
    descargadas vía web (unitaria / job mensual): misma colección, mismo
    schema canónico, misma clave de upsert.

USO
---
Desde CMD o PowerShell de Windows (no desde el REPL `>>>`):

    python ingestar_csv_a_mongo.py --config config_ingesta.json
    py     ingestar_csv_a_mongo.py --config config_ingesta.json

Cualquier campo del JSON puede sobreescribirse por CLI, p.ej.:

    py ingestar_csv_a_mongo.py --config config_ingesta.json --csv otro.csv --dry-run

PLANTILLA DE CONFIG
-------------------
Ver `config_ingesta.example.json` en esta misma carpeta.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# -----------------------------------------------------------------------------
# Mapeo cabeceras CSV (XSD AEAT) -> campos canónicos en `facturas_sii`
# -----------------------------------------------------------------------------
COLUMN_MAP: dict[str, str] = {
    "PeriodoEjercicio": "ejercicio",
    "PeriodoPeriodo": "periodo",
    "IDEmisorFacturaNIF": "nif_emisor",
    "IDEmisorFacturaNombre": "nombre_emisor",
    "NumSerieFacturaEmisor": "num_serie_factura",
    "NumSerieFacturaEmisorFin": "num_serie_factura_fin",
    "FechaExpedicionFacturaEmisor": "fecha_expedicion",
    "TipoFactura": "tipo_factura",
    "ClaveRegimenEspecial": "clave_regimen_especial",
    "ImporteTotal": "importe_total",
    "DescripcionOperacion": "descripcion_operacion",
    "FechaOperacion": "fecha_operacion",
    "BaseImponible": "base_imponible",
    "TipoImpositivo": "tipo_impositivo",
    "CuotaRepercutida": "cuota_repercutida",
    "ContraparteNIF": "contraparte_nif",
    "ContraparteNombre": "contraparte_nombre",
    "EstadoFactura": "estado_factura",
    "CSVAEAT": "csv_aeat",
    "NumRegistroPresentacion": "num_registro_presentacion",
    "TimestampPresentacion": "timestamp_presentacion",
}

NUMERIC_FIELDS = {
    "importe_total",
    "base_imponible",
    "tipo_impositivo",
    "cuota_repercutida",
}


# -----------------------------------------------------------------------------
# Utilidades
# -----------------------------------------------------------------------------
def _parse_amount(raw: str) -> float | None:
    """Convierte a float aceptando coma o punto decimal. Vacío -> None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # "1.234,56" -> "1234.56" ; "1234,56" -> "1234.56"
    if s.count(",") == 1 and s.count(".") >= 1:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def map_row(row: dict[str, str], nif_titular: str, nombre_titular: str) -> dict[str, Any] | None:
    """Convierte una fila CSV (claves XSD) en un documento canónico de Mongo."""
    doc: dict[str, Any] = {}
    for csv_col, canon in COLUMN_MAP.items():
        if csv_col not in row:
            continue
        val = row[csv_col]
        if val is None:
            continue
        val = str(val).strip()
        if not val:
            continue
        if canon in NUMERIC_FIELDS:
            doc[canon] = _parse_amount(val)
        else:
            doc[canon] = val

    if not doc.get("num_serie_factura"):
        return None

    doc["nif_titular"] = nif_titular
    if nombre_titular:
        doc["nombre_titular"] = nombre_titular
    return doc


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        raise SystemExit(f"[ERROR] No existe el fichero de config: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_cli_overrides(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Permite sobrescribir cualquier campo del JSON desde línea de comandos."""
    overrides = {
        "csv": args.csv,
        "mongo_url": args.mongo_url,
        "db": args.db,
        "coleccion": args.coleccion,
        "nif_titular": args.nif_titular,
        "nombre_titular": args.nombre_titular,
        "batch_size": args.batch_size,
    }
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    return cfg


def validar_config(cfg: dict[str, Any]) -> None:
    required = ["csv", "mongo_url", "db", "coleccion", "nif_titular"]
    falta = [k for k in required if not cfg.get(k)]
    if falta:
        raise SystemExit(
            f"[ERROR] Faltan campos obligatorios en config o CLI: {', '.join(falta)}"
        )


def crear_indices(coll) -> None:
    """Crea los mismos índices que el backend para no romper consultas."""
    coll.create_index("num_serie_factura", unique=True)
    coll.create_index([("ejercicio", 1), ("periodo", 1)])
    coll.create_index([("nif_titular", 1), ("ejercicio", 1), ("periodo", 1)])


def ingestar(cfg: dict[str, Any], dry_run: bool = False) -> int:
    csv_path = Path(cfg["csv"])
    if not csv_path.is_file():
        raise SystemExit(f"[ERROR] No existe el CSV: {csv_path}")

    nif_titular = cfg["nif_titular"]
    nombre_titular = cfg.get("nombre_titular", "")
    batch_size = int(cfg.get("batch_size", 2000))

    print(f"[INFO] CSV:            {csv_path}")
    print(f"[INFO] Mongo URL:      {cfg['mongo_url']}")
    print(f"[INFO] BD / colección: {cfg['db']} / {cfg['coleccion']}")
    print(f"[INFO] NIF titular:    {nif_titular}")
    print(f"[INFO] Nombre titular: {nombre_titular or '(sin definir)'}")
    print(f"[INFO] Batch size:     {batch_size}")
    print(f"[INFO] Dry-run:        {dry_run}")
    print()

    coll = None
    if not dry_run:
        try:
            from pymongo import MongoClient, UpdateOne  # noqa: WPS433
        except ImportError:
            raise SystemExit(
                "[ERROR] Falta `pymongo`. Instálalo con:  pip install pymongo"
            )
        mongo = MongoClient(cfg["mongo_url"])
        coll = mongo[cfg["db"]][cfg["coleccion"]]
        crear_indices(coll)

    leidas = 0
    mapeadas = 0
    saltadas = 0
    inserted = 0
    modified = 0
    matched = 0
    upserts: list[Any] = []
    ahora = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    # `utf-8-sig` por si el CSV viene con BOM
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        # delimitador `|` (como emite la colección Postman). Si detectamos
        # coma en la cabecera, asumimos CSV estándar.
        sniff = f.readline()
        f.seek(0)
        delim = "|" if "|" in sniff else ","
        reader = csv.DictReader(f, delimiter=delim)

        if not reader.fieldnames:
            raise SystemExit("[ERROR] CSV vacío o sin cabecera.")

        # Validamos que al menos NumSerieFacturaEmisor está presente
        if "NumSerieFacturaEmisor" not in reader.fieldnames:
            raise SystemExit(
                "[ERROR] El CSV no contiene la columna 'NumSerieFacturaEmisor'.\n"
                f"        Cabeceras encontradas: {reader.fieldnames}\n"
                "        Recuerda que debe ser el CSV generado por extraer_csv.py."
            )

        from pymongo import UpdateOne  # noqa: WPS433 (lazy import también en dry-run para no inflar deps)

        for row in reader:
            leidas += 1
            doc = map_row(row, nif_titular, nombre_titular)
            if not doc:
                saltadas += 1
                continue
            mapeadas += 1

            if dry_run:
                if leidas <= 3:
                    print(f"[DRY] Fila {leidas} -> {doc}")
                continue

            upserts.append(
                UpdateOne(
                    {"num_serie_factura": doc["num_serie_factura"]},
                    {"$set": {
                        **doc,
                        "fuente_ultima": "newman_csv",
                        "ultima_actualizacion": ahora,
                    }},
                    upsert=True,
                )
            )

            if len(upserts) >= batch_size:
                res = coll.bulk_write(upserts, ordered=False)
                inserted += res.upserted_count
                modified += res.modified_count
                matched += res.matched_count
                upserts.clear()
                _progreso(leidas, t0)

        # Flush final
        if upserts and not dry_run:
            res = coll.bulk_write(upserts, ordered=False)
            inserted += res.upserted_count
            modified += res.modified_count
            matched += res.matched_count

    dt = time.time() - t0
    print()
    print("=" * 60)
    print(f"Filas leídas:       {leidas}")
    print(f"Filas mapeadas:     {mapeadas}")
    print(f"Filas saltadas:     {saltadas} (sin num_serie_factura)")
    if not dry_run:
        print(f"Nuevas insertadas:  {inserted}")
        print(f"Modificadas:        {modified}")
        print(f"Sin cambios:        {max(0, matched - modified)}")
    print(f"Duración total:     {dt:.1f} s ({leidas/dt:.0f} filas/s)" if dt > 0 else "")
    print("=" * 60)
    return 0


def _progreso(n: int, t0: float) -> None:
    dt = max(0.001, time.time() - t0)
    print(f"  [{n:>8}] {n/dt:>6.0f} filas/s")


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingiere CSV de facturas emitidas en MongoDB.")
    p.add_argument("--config", required=True, help="Fichero JSON con la configuración.")
    p.add_argument("--csv")
    p.add_argument("--mongo-url")
    p.add_argument("--db")
    p.add_argument("--coleccion")
    p.add_argument("--nif-titular")
    p.add_argument("--nombre-titular")
    p.add_argument("--batch-size", type=int)
    p.add_argument("--dry-run", action="store_true",
                   help="No escribe en Mongo: sólo mapea y enseña las 3 primeras filas.")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cfg = load_config(Path(args.config))
    cfg = apply_cli_overrides(cfg, args)
    validar_config(cfg)
    return ingestar(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
