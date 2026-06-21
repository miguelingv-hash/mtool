"""
router_pagos_ventanilla.py — Módulo "Pagos Ventanilla".

Endpoints (bajo /api/pagos-ventanilla):
  POST   /upload                          — subir CSV → upload_id + preview
  POST   /generate                        — genera PDFs a partir de upload_id
  GET    /jobs                            — lista de trabajos del usuario
  GET    /jobs/{id}                       — detalle del trabajo
  GET    /jobs/{id}/download              — ZIP completo
  GET    /jobs/{id}/files/{filename}      — PDF individual
  GET    /pagos/search                    — buscador del histórico
  GET    /jobs/auth/download-token        — JWT corto para <iframe> y <a href>

Colecciones Mongo:
  pagos_ventanilla_uploads, pagos_ventanilla_jobs, pagos_ventanilla_pagos
"""
from __future__ import annotations

import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from auth import (
    COOKIE_ACCESS,
    create_access_token,
    decode_token,
    require_permission,
)
from pagos_ventanilla_pdf import (
    LIMITE_CORREOS,
    SOCIEDADES,
    build_pdf,
    build_referencia,
    parse_csv_rows,
    _add_months,
)

router = APIRouter(prefix="/pagos-ventanilla", tags=["pagos-ventanilla"])

STORAGE_DIR = Path("/app/backend/storage")
PV_DIR = STORAGE_DIR / "pagos_ventanilla"
PV_DIR.mkdir(parents=True, exist_ok=True)

_SAFE = re.compile(r"[^A-Za-z0-9._\- ]+")


def safe_filename(name: str) -> str:
    return _SAFE.sub("_", (name or "").strip()).strip(" .") or "documento"


def _db(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.mongo_db


def _uid(user: dict) -> str:
    return str(user.get("_id") or user.get("id") or "")


def _require_view(user: dict = Depends(require_permission("pagos_ventanilla.view"))) -> dict:
    return user


def _require_manage(user: dict = Depends(require_permission("pagos_ventanilla.manage"))) -> dict:
    return user


class GenerateRequest(BaseModel):
    upload_id: str
    indices: Optional[List[int]] = None  # filtra filas por _row_index (1-based)


# =============================================================================
# Upload (parse + preview)
# =============================================================================
@router.post("/upload")
async def pv_upload(request: Request, file: UploadFile = File(...),
                    user: dict = Depends(_require_manage)):
    name = (file.filename or "").lower()
    if not (name.endswith(".csv") or name.endswith(".txt")):
        raise HTTPException(400, "El archivo debe ser CSV (separador ;)")
    raw = await file.read()
    db = _db(request); uid = _uid(user)
    try:
        rows = parse_csv_rows(raw)
    except Exception as e:
        raise HTTPException(400, f"CSV inválido: {e}")
    if not rows:
        raise HTTPException(400, "El CSV no contiene filas válidas")

    upload_id = str(uuid.uuid4())
    user_dir = PV_DIR / uid / "uploads"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / f"{upload_id}.csv").write_bytes(raw)

    # Resumen por sociedad
    by_soc: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        s = r["sociedad"]
        by_soc.setdefault(s, {"sociedad": s, "rows": 0, "importe_total": 0.0})
        by_soc[s]["rows"] += 1
        by_soc[s]["importe_total"] += r["importe"]
    for v in by_soc.values():
        v["importe_total"] = round(v["importe_total"], 2)

    # Preview (primeras 50 filas)
    preview = [{
        "idx": r["_row_index"],
        "sociedad": r["sociedad"],
        "nombre_cliente": r["nombre_cliente"],
        "cif_nif": r["cif_nif"],
        "numero_factura": r["numero_factura"],
        "importe": round(r["importe"], 2),
        "fecha_emision_factura": r["fecha_emision_factura"].isoformat() if r["fecha_emision_factura"] else "",
        "fecha_limite_pago": r["fecha_limite_pago"].isoformat() if r["fecha_limite_pago"] else "",
    } for r in rows[:50]]

    doc = {
        "_id": upload_id, "user_id": uid,
        "filename": file.filename or "pagos.csv",
        "path": str(user_dir / f"{upload_id}.csv"),
        "row_count": len(rows),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.pagos_ventanilla_uploads.insert_one(doc)
    return {"id": upload_id, "filename": doc["filename"], "row_count": len(rows),
            "by_sociedad": list(by_soc.values()), "preview": preview}


# =============================================================================
# Generate
# =============================================================================
@router.post("/generate")
async def pv_generate(payload: GenerateRequest, request: Request,
                      user: dict = Depends(_require_manage)):
    db = _db(request); uid = _uid(user)
    upload = await db.pagos_ventanilla_uploads.find_one({"_id": payload.upload_id, "user_id": uid})
    if not upload:
        raise HTTPException(404, "Subida no encontrada")
    csv_path = Path(upload["path"])
    if not csv_path.exists():
        raise HTTPException(404, "Archivo CSV no disponible")
    try:
        rows = parse_csv_rows(csv_path.read_bytes())
    except Exception as e:
        raise HTTPException(400, f"CSV inválido: {e}")
    if payload.indices:
        wanted = set(payload.indices)
        rows = [r for r in rows if r["_row_index"] in wanted]
    if not rows:
        raise HTTPException(400, "No hay filas seleccionadas para generar")

    job_id = str(uuid.uuid4())
    output_dir = PV_DIR / uid / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    files: List[str] = []
    errors: List[Dict[str, Any]] = []
    pagos_bulk: List[Dict[str, Any]] = []

    for r in rows:
        try:
            pdf_bytes = build_pdf(r)
            soc = SOCIEDADES[r["sociedad"]]
            fecha_validez = _add_months(r["fecha_emision_doc"], int(r["validez_meses"] or 5))
            fname = safe_filename(
                f"DocPago_{r['sociedad']}_{r['numero_factura']}_{r['cif_nif']}"
            ) + ".pdf"
            (output_dir / fname).write_bytes(pdf_bytes)
            files.append(fname)
            pago = {
                "_id": str(uuid.uuid4()),
                "user_id": uid,
                "job_id": job_id,
                "sociedad": r["sociedad"],
                "nombre_cliente": r["nombre_cliente"],
                "cif_nif": r["cif_nif"],
                "direccion_social": r["direccion_social"],
                "direccion_suministro": r["direccion_suministro"],
                "cuenta_contrato": r["cuenta_contrato"],
                "numero_factura": r["numero_factura"],
                "referencia": build_referencia(r),
                "fecha_emision_factura": r["fecha_emision_factura"].isoformat() if r["fecha_emision_factura"] else None,
                "fecha_emision_doc": r["fecha_emision_doc"].isoformat(),
                "fecha_limite_pago": r["fecha_limite_pago"].isoformat() if r["fecha_limite_pago"] else None,
                "fecha_validez": fecha_validez.isoformat(),
                "importe": round(r["importe"], 2),
                "validez_meses": int(r["validez_meses"] or 5),
                "sufijo": r["sufijo"],
                "idioma": r["idioma"],
                "pdf_filename": fname,
                "pdf_path": str(output_dir / fname),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "estado": "OK",
            }
            pagos_bulk.append(pago)
        except Exception as e:
            errors.append({"idx": r["_row_index"], "error": str(e)})

    if pagos_bulk:
        await db.pagos_ventanilla_pagos.insert_many(pagos_bulk)

    # ZIP
    zip_path = output_dir / "_all.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in files:
            zf.write(output_dir / fn, fn)

    job_doc = {
        "_id": job_id, "user_id": uid,
        "csv_filename": upload["filename"],
        "row_count": len(rows),
        "generated_count": len(files),
        "error_count": len(errors),
        "errors": errors,
        "files": files,
        "by_sociedad": _aggregate_by_soc(rows),
        "status": "completado" if not errors else ("parcial" if files else "fallido"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.pagos_ventanilla_jobs.insert_one(job_doc)
    return {"id": job_id, "status": job_doc["status"],
            "generated_count": len(files), "error_count": len(errors),
            "files": files}


def _aggregate_by_soc(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    acc: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        s = r["sociedad"]
        acc.setdefault(s, {"sociedad": s, "rows": 0, "importe_total": 0.0})
        acc[s]["rows"] += 1
        acc[s]["importe_total"] += r["importe"]
    for v in acc.values():
        v["importe_total"] = round(v["importe_total"], 2)
    return list(acc.values())


# =============================================================================
# Jobs
# =============================================================================
@router.get("/jobs")
async def list_jobs(request: Request, user: dict = Depends(_require_view)):
    db = _db(request); uid = _uid(user)
    cursor = db.pagos_ventanilla_jobs.find({"user_id": uid}, {
        "_id": 1, "csv_filename": 1, "row_count": 1, "generated_count": 1,
        "error_count": 1, "status": 1, "created_at": 1, "by_sociedad": 1,
    }).sort("created_at", -1).limit(200)
    return [{"id": j["_id"], **{k: j.get(k) for k in
            ["csv_filename", "row_count", "generated_count", "error_count",
             "status", "created_at", "by_sociedad"]}} async for j in cursor]


@router.get("/jobs/auth/download-token")
async def download_token(user: dict = Depends(_require_view)):
    return {"token": create_access_token(_uid(user), user.get("email", ""))}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, user: dict = Depends(_require_view)):
    db = _db(request); uid = _uid(user)
    j = await db.pagos_ventanilla_jobs.find_one({"_id": job_id, "user_id": uid})
    if not j:
        raise HTTPException(404, "Trabajo no encontrado")
    return {"id": j["_id"], **{k: j.get(k) for k in
            ["csv_filename", "row_count", "generated_count", "error_count",
             "errors", "files", "by_sociedad", "status", "created_at"]}}


def _resolve_uid(request: Request, token: Optional[str]) -> str:
    if token:
        return decode_token(token, "access")["sub"]
    cookie = request.cookies.get(COOKIE_ACCESS)
    if not cookie:
        raise HTTPException(401, "No autenticado")
    return decode_token(cookie, "access")["sub"]


@router.get("/jobs/{job_id}/download")
async def download_zip(job_id: str, request: Request, token: Optional[str] = None):
    uid = _resolve_uid(request, token)
    db = _db(request)
    j = await db.pagos_ventanilla_jobs.find_one({"_id": job_id, "user_id": uid})
    if not j:
        raise HTTPException(404, "Trabajo no encontrado")
    zip_path = PV_DIR / uid / job_id / "_all.zip"
    if not zip_path.exists():
        raise HTTPException(404, "ZIP no encontrado")
    return FileResponse(str(zip_path), media_type="application/zip",
                        filename=f"PagosVentanilla_{job_id[:8]}.zip")


@router.get("/jobs/{job_id}/files/{filename}")
async def download_file(job_id: str, filename: str, request: Request,
                        token: Optional[str] = None):
    uid = _resolve_uid(request, token)
    db = _db(request)
    j = await db.pagos_ventanilla_jobs.find_one({"_id": job_id, "user_id": uid})
    if not j:
        raise HTTPException(404, "Trabajo no encontrado")
    file_path = PV_DIR / uid / job_id / filename
    if not file_path.exists() or not str(file_path.resolve()).startswith(str(PV_DIR.resolve())):
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(str(file_path), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{safe_filename(filename)}"'})


# =============================================================================
# Search del histórico (filtros)
# =============================================================================
@router.get("/pagos/search")
async def pagos_search(
    request: Request,
    user: dict = Depends(_require_view),
    sociedad: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    importe_min: Optional[float] = None,
    importe_max: Optional[float] = None,
    cif_nif: Optional[str] = None,
    numero_factura: Optional[str] = None,
    referencia: Optional[str] = None,
    estado: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
):
    db = _db(request); uid = _uid(user)
    q: Dict[str, Any] = {"user_id": uid}
    if sociedad:
        q["sociedad"] = sociedad.upper()
    if estado:
        q["estado"] = estado.upper()
    if cif_nif:
        q["cif_nif"] = {"$regex": cif_nif, "$options": "i"}
    if numero_factura:
        q["numero_factura"] = {"$regex": numero_factura, "$options": "i"}
    if referencia:
        q["referencia"] = {"$regex": referencia, "$options": "i"}
    if fecha_desde:
        q.setdefault("fecha_emision_doc", {})["$gte"] = fecha_desde
    if fecha_hasta:
        q.setdefault("fecha_emision_doc", {})["$lte"] = fecha_hasta
    if importe_min is not None:
        q.setdefault("importe", {})["$gte"] = importe_min
    if importe_max is not None:
        q.setdefault("importe", {})["$lte"] = importe_max

    page = max(1, page); limit = max(1, min(500, limit))
    total = await db.pagos_ventanilla_pagos.count_documents(q)
    cursor = (db.pagos_ventanilla_pagos.find(q, {
        "_id": 1, "job_id": 1, "sociedad": 1, "nombre_cliente": 1, "cif_nif": 1,
        "numero_factura": 1, "referencia": 1, "importe": 1,
        "fecha_emision_factura": 1, "fecha_emision_doc": 1, "fecha_limite_pago": 1,
        "estado": 1, "pdf_filename": 1, "created_at": 1,
    }).sort("created_at", -1).skip((page - 1) * limit).limit(limit))
    items = []
    async for p in cursor:
        items.append({"id": p["_id"], **{k: p.get(k) for k in [
            "job_id", "sociedad", "nombre_cliente", "cif_nif", "numero_factura",
            "referencia", "importe", "fecha_emision_factura", "fecha_emision_doc",
            "fecha_limite_pago", "estado", "pdf_filename", "created_at"]}})
    return {"items": items, "total": total, "page": page, "limit": limit,
            "pages": max(1, (total + limit - 1) // limit)}


@router.get("/csv-template")
async def csv_template(_: dict = Depends(_require_view)):
    """Devuelve una plantilla CSV de ejemplo."""
    from fastapi.responses import PlainTextResponse
    header = ";".join([
        "sociedad", "nombre_cliente", "cif_nif", "direccion_social",
        "direccion_suministro", "cuenta_contrato", "numero_factura",
        "fecha_emision_factura", "fecha_emision_doc", "fecha_limite_pago",
        "importe", "validez_meses", "sufijo", "idioma",
    ])
    rows = [
        "TTE;Juan Pérez García;12345678Z;Calle Mayor 5, 33012 Oviedo;CL Sagrado Corazón 12, 33208 Gijón;CC0001234;2026A0000123;13.03.2026;20.03.2026;20.05.2026;109,95;5;510;es",
        "BASER;Empresa Ejemplo S.L.;B12345678;Av. de la Costa 25, 33203 Gijón;Pol. Asipo Nave 12, 33428 Llanera;CC0007890;2026B0000456;05.02.2026;20.02.2026;20.04.2026;850,00;5;510;es",
    ]
    return PlainTextResponse(header + "\n" + "\n".join(rows), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="plantilla_pagos_ventanilla.csv"'})
