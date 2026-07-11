"""
imports_log.py — Audit Trail de importaciones a la BD
======================================================

Registra cada importación de facturas (SII y Comercial) para trazabilidad:
qué fichero cargó qué facturas, quién y cuándo, y con qué resultado.

Se persiste en la colección MongoDB `imports_log`. Los detalles de errores por
fila se recortan a `MAX_ERRORES_GUARDADOS` (100) para no inflar los documentos.

Uso:
  1) `import_id = await start_import(...)` al arrancar la importación.
  2) `await finish_import(import_id, status="done"|"error", **totales)` al
     terminar. Si falla, pasar `error_message`.
  3) Opcional durante el parseo: `await add_import_errors(import_id, errores)`.

Los endpoints administrativos para consulta viven en `router_admin.py`:
  GET  /api/admin/imports-log            → listado paginado + filtros
  GET  /api/admin/imports-log/{id}       → detalle con errores
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

# Máximo número de errores por fila que guardamos en el log (evita documentos
# gigantes cuando un CSV entero es basura → guardamos los 100 primeros).
MAX_ERRORES_GUARDADOS = 100
COLLECTION = "imports_log"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Índices para el listado y filtros habituales del audit trail."""
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index([("timestamp_start", -1)])
    await db[COLLECTION].create_index([("origen", 1), ("timestamp_start", -1)])
    await db[COLLECTION].create_index([("user_id", 1), ("timestamp_start", -1)])
    await db[COLLECTION].create_index([("status", 1), ("timestamp_start", -1)])


async def start_import(
    db: AsyncIOMotorDatabase,
    *,
    origen: str,                        # "sii" | "comercial"
    fuente: str,                        # "ui_upload" | "cli" | "conciliacion_newman" | "consulta_mensual_aeat" | "batch_csv" | "consulta_unitaria"
    file_name: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    nif_titular: Optional[str] = None,
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    job_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    """Crea un documento de audit trail en estado `running` y devuelve su id.

    `origen` = colección afectada (`sii` o `comercial`).
    `fuente` = flujo/entrypoint que originó la importación (UI, CLI, AEAT…).
    """
    doc_id = uuid.uuid4().hex
    now = _now_iso()
    doc: dict[str, Any] = {
        "id": doc_id,
        "origen": origen,
        "fuente": fuente,
        "file_name": file_name,
        "file_size_bytes": file_size_bytes,
        "user_id": user_id,
        "user_email": user_email,
        "nif_titular": nif_titular,
        "ejercicio": ejercicio,
        "periodo": periodo,
        "job_id": job_id,
        "status": "running",
        "total_procesados": 0,
        "insertados": 0,
        "actualizados": 0,
        "errores_count": 0,
        "errores": [],
        "error_message": None,
        "timestamp_start": now,
        "timestamp_end": None,
        "duration_ms": None,
        "extra": extra or {},
    }
    await db[COLLECTION].insert_one(doc)
    return doc_id


async def finish_import(
    db: AsyncIOMotorDatabase,
    import_id: Optional[str],
    *,
    status: str = "done",               # "done" | "error"
    total_procesados: Optional[int] = None,
    insertados: Optional[int] = None,
    actualizados: Optional[int] = None,
    errores_count: Optional[int] = None,
    error_message: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Cierra un audit trail con status y totales."""
    if not import_id:
        return
    end_iso = _now_iso()
    # Calcular duración
    doc = await db[COLLECTION].find_one({"id": import_id}, {"timestamp_start": 1})
    duration_ms: Optional[int] = None
    if doc and doc.get("timestamp_start"):
        try:
            start_dt = datetime.fromisoformat(doc["timestamp_start"])
            duration_ms = int(
                (datetime.now(timezone.utc) - start_dt).total_seconds() * 1000
            )
        except (ValueError, TypeError):
            duration_ms = None

    update: dict[str, Any] = {
        "status": status,
        "timestamp_end": end_iso,
        "duration_ms": duration_ms,
    }
    if total_procesados is not None:
        update["total_procesados"] = int(total_procesados)
    if insertados is not None:
        update["insertados"] = int(insertados)
    if actualizados is not None:
        update["actualizados"] = int(actualizados)
    if errores_count is not None:
        update["errores_count"] = int(errores_count)
    if error_message is not None:
        update["error_message"] = str(error_message)[:2000]
    if extra:
        # Merge en el subdoc `extra` sin borrar lo previo.
        for k, v in extra.items():
            update[f"extra.{k}"] = v

    await db[COLLECTION].update_one({"id": import_id}, {"$set": update})


async def add_import_errors(
    db: AsyncIOMotorDatabase,
    import_id: Optional[str],
    errores: list[dict],
) -> None:
    """Añade errores por fila (máx `MAX_ERRORES_GUARDADOS` en total)."""
    if not import_id or not errores:
        return
    # Normalizar cada error a un dict pequeño (fila, motivo, num_serie_factura?, datos?)
    normalizados: list[dict] = []
    for e in errores[:MAX_ERRORES_GUARDADOS]:
        if not isinstance(e, dict):
            normalizados.append({"motivo": str(e)[:500]})
            continue
        norm = {
            "fila": e.get("fila"),
            "num_serie_factura": e.get("num_serie_factura"),
            "motivo": (e.get("motivo") or "")[:500],
        }
        if "datos" in e:
            # Guardar sólo un resumen (evitar cargar todo el dict de la fila).
            datos = e["datos"] if isinstance(e["datos"], dict) else {}
            norm["datos"] = {
                k: v for k, v in list(datos.items())[:6]
            }
        normalizados.append(norm)

    # $push con $slice para mantener máximo MAX_ERRORES_GUARDADOS y a la vez
    # actualizar errores_count con el número real total pasado.
    await db[COLLECTION].update_one(
        {"id": import_id},
        {
            "$push": {
                "errores": {
                    "$each": normalizados,
                    "$slice": MAX_ERRORES_GUARDADOS,
                },
            },
            "$inc": {"errores_count": len(errores)},
        },
    )
