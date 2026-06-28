"""Utilidades compartidas para los scripts CLI de carga directa
(`import_newman_sii.py` y `import_comercial.py`).

Mantenemos esta lógica en su propio módulo para evitar duplicación entre los
dos scripts y para que se pueda testear desde pytest si hace falta. Toda la
parte específica de FastAPI (HTTP, Forms, UploadFile) queda fuera: aquí sólo
tenemos lectura desde disco, parseo (reusando los parsers oficiales del
backend) y bulk insert/upsert directo contra Mongo.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from motor.motor_asyncio import AsyncIOMotorClient

# Carga el .env del backend antes de leer las env vars. En ejecución dentro
# del contenedor las vars ya están en el entorno (vienen de docker-compose
# env_file), pero al ejecutar el script en local también funciona.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


LOG_FORMAT = "%(asctime)s  %(levelname)-7s  %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(name: str) -> logging.Logger:
    """Logger sencillo a stdout pensado para que el output sea legible en una
    terminal SSH y a la vez `grep`able si redirige a un fichero (cron)."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
    return logging.getLogger(name)


@contextmanager
def exclusive_lock(lock_name: str):
    """Lock por fichero en /tmp. Si un segundo proceso del mismo script
    intenta arrancar, recibe `BlockingIOError` y debe abortar limpiamente.

    Pasamos el path completo en lugar de sólo el basename para que dos scripts
    distintos (newman vs comercial) no se bloqueen entre sí.
    """
    lock_path = Path("/tmp") / lock_name
    fh = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"Hay otra ejecución en curso (lock {lock_path}). "
                f"Espera a que termine o borra el fichero si estás seguro de "
                f"que no hay otro proceso."
            ) from exc
        fh.write(str(os.getpid()))
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def get_mongo_db():
    """Devuelve el handle Motor a la BD del backend usando las mismas vars de
    entorno que `server.py`. Si falta algo aborta con exit code 2 (fallo de
    conexión / config), distinto de exit 1 (errores de parsing) — útil para
    cron alerting.
    """
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print(
            "ERROR: faltan MONGO_URL o DB_NAME en el entorno. "
            "Ejecuta este script dentro del contenedor backend (lleva las "
            "vars de docker-compose env_file).",
            file=sys.stderr,
        )
        sys.exit(2)
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=10_000)
    return client[db_name]


def batched(iterable: Iterable, n: int):
    """Itertools.batched (py 3.12+) reimplementado para compatibilidad. Yield
    listas de tamaño máximo `n` desde cualquier iterable."""
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


async def bulk_upsert(
    db,
    coleccion: str,
    docs: list[dict],
    fuente: str,
    log: logging.Logger,
    batch_size: int = 1000,
):
    """Bulk upsert por lotes con barra de progreso en stdout.

    Idempotente por clave única `num_serie_factura`. No mantiene histórico
    (`$push versiones`) para que sea rápido en cargas masivas — coherente con
    `upsert_facturas_bulk` del backend.

    Devuelve un dict con conteos: `{procesadas, insertadas, modificadas}`.
    """
    from datetime import datetime, timezone
    from pymongo import UpdateOne

    now_iso = datetime.now(timezone.utc).isoformat()
    total = len(docs)
    procesadas = 0
    insertadas = 0
    modificadas = 0
    t0 = time.time()

    for batch in batched(docs, batch_size):
        ops = []
        for d in batch:
            if not d.get("num_serie_factura"):
                continue
            ops.append(
                UpdateOne(
                    {"num_serie_factura": d["num_serie_factura"]},
                    {"$set": {
                        **d,
                        "fuente_ultima": fuente,
                        "ultima_actualizacion": now_iso,
                    }},
                    upsert=True,
                )
            )
        if not ops:
            continue
        result = await db[coleccion].bulk_write(ops, ordered=False)
        insertadas += result.upserted_count
        modificadas += result.modified_count
        procesadas += len(ops)
        elapsed = time.time() - t0
        rate = procesadas / elapsed if elapsed > 0 else 0
        pct = (procesadas / total) * 100 if total else 100
        log.info(
            "  ⏳ %s · %d/%d (%.1f%%) · %.0f docs/s · inserted=%d modified=%d",
            coleccion, procesadas, total, pct, rate, insertadas, modificadas,
        )

    return {
        "procesadas": procesadas,
        "insertadas": insertadas,
        "modificadas": modificadas,
        "duracion_s": time.time() - t0,
    }


def cleanup_csv(csv_path: Path, delete_after: bool, log: logging.Logger):
    """Borra el CSV de origen si `delete_after=True` y la carga fue OK. El
    flag por defecto es True para los dos scripts (decisión del usuario), pero
    permitimos `--keep-csv` por si se quiere preservar."""
    if not delete_after:
        log.info("CSV conservado en %s (--keep-csv)", csv_path)
        return
    try:
        csv_path.unlink()
        log.info("CSV de origen borrado tras carga exitosa: %s", csv_path)
    except OSError as exc:
        log.warning("No se pudo borrar el CSV: %s", exc)


def run_async(coro):
    """Wrapper para `asyncio.run` que captura `KeyboardInterrupt` con código
    de salida 130 (convención POSIX) en lugar de stacktrace ruidoso."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        sys.exit(130)
