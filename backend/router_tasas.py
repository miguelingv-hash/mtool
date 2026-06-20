"""
router_tasas.py — Módulo "Tasas Municipales" (portado desde la app migtool)

Endpoints (bajo /api):
  GET    /tasas-municipales/municipios
  POST   /tasas-municipales/municipios
  PUT    /tasas-municipales/municipios/{codigo}
  DELETE /tasas-municipales/municipios/{codigo}

  POST   /tasas-municipales/upload         — subida CSV → upload_id + detección
  POST   /tasas-municipales/generate       — genera PDFs a partir de upload_id

  GET    /tasas-municipales/jobs           — lista de trabajos del usuario
  GET    /tasas-municipales/jobs/{id}      — detalle
  GET    /tasas-municipales/jobs/{id}/download           — ZIP completo
  GET    /tasas-municipales/jobs/{id}/files/{filename}   — PDF individual
  GET    /tasas-municipales/jobs/auth/download-token     — JWT corto para <a href>

  GET    /tasas-municipales/settings              — admin
  PUT    /tasas-municipales/settings              — admin
  GET    /tasas-municipales/settings/public       — cualquier user logueado
  GET    /tasas-municipales/sharepoint/input-files
  POST   /tasas-municipales/upload-from-sharepoint

Colecciones Mongo (prefijo `tasas_` para no colisionar con Corporate App):
  tasas_jobs, tasas_municipios, tasas_settings (`_id="sharepoint"`),
  tasas_uploads.
"""

from __future__ import annotations

import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from auth import (
    COOKIE_ACCESS,
    create_access_token,
    decode_token,
    get_current_user,
    require_permission,
)
from sharepoint_client import filename_for_output, get_client as get_sp_client
from tasas_pdf import aggregate_by_municipio, build_pdf as build_tasas_pdf, parse_csv_rows

router = APIRouter(prefix="/tasas-municipales", tags=["tasas-municipales"])

# Almacén local de PDFs y subidas
STORAGE_DIR = Path("/app/backend/storage")
JOBS_DIR = STORAGE_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

_SAFE = re.compile(r"[^A-Za-z0-9._\- ]+")


def safe_filename(name: str) -> str:
    return _SAFE.sub("_", (name or "").strip()).strip(" .") or "documento"


def _db(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.mongo_db


def _uid(user: dict) -> str:
    return str(user.get("_id") or user.get("id") or "")


def _require_view(user: dict = Depends(require_permission("tasas.view"))) -> dict:
    return user


def _require_manage(user: dict = Depends(require_permission("tasas.manage"))) -> dict:
    return user


def _require_admin(user: dict = Depends(require_permission("tasas.admin"))) -> dict:
    return user


# =============================================================================
# Models
# =============================================================================
class MunicipioModel(BaseModel):
    codigo: str = Field(min_length=1, max_length=20)
    nombre: str = Field(min_length=1, max_length=200)
    calle: Optional[str] = ""
    numero: Optional[str] = ""
    codigo_postal: Optional[str] = ""
    provincia: Optional[str] = ""
    telefono_contacto: Optional[str] = ""
    persona_contacto: Optional[str] = ""


class TasasGenerateRequest(BaseModel):
    upload_id: str
    codigos: Optional[List[str]] = None
    upload_to_sharepoint: bool = False


class SharepointSettings(BaseModel):
    enabled_input: bool = False
    enabled_output: bool = False
    mock_mode: bool = True
    tenant_id: Optional[str] = ""
    client_id: Optional[str] = ""
    client_secret: Optional[str] = ""
    site_url: Optional[str] = ""
    input_folder: Optional[str] = "/Tasas/Entrada"
    output_folder: Optional[str] = "/Tasas/Salida"
    atencion_telefono: Optional[str] = "900 907 000"
    logos_by_sociedad: Optional[Dict[str, str]] = None


class SharepointImportRequest(BaseModel):
    file_id: str


# =============================================================================
# Municipios CRUD
# =============================================================================
@router.get("/municipios")
async def list_municipios(request: Request, user: dict = Depends(_require_view),
                          page: int = 1, limit: int = 25, q: str = ""):
    db = _db(request)
    uid = _uid(user)
    page = max(1, page); limit = max(1, min(200, limit))
    query: Dict[str, Any] = {"user_id": uid}
    if q:
        query["$or"] = [
            {"codigo": {"$regex": q, "$options": "i"}},
            {"nombre": {"$regex": q, "$options": "i"}},
            {"provincia": {"$regex": q, "$options": "i"}},
        ]
    total = await db.tasas_municipios.count_documents(query)
    cursor = (db.tasas_municipios.find(query, {"codigo": 1, "nombre": 1, "calle": 1, "numero": 1,
                                               "codigo_postal": 1, "provincia": 1,
                                               "telefono_contacto": 1, "persona_contacto": 1})
              .sort("codigo", 1).skip((page - 1) * limit).limit(limit))
    items = []
    async for m in cursor:
        items.append({k: m.get(k, "") for k in
                      ["codigo", "nombre", "calle", "numero", "codigo_postal",
                       "provincia", "telefono_contacto", "persona_contacto"]})
    return {"items": items, "total": total, "page": page, "limit": limit,
            "pages": max(1, (total + limit - 1) // limit)}


@router.post("/municipios")
async def create_municipio(payload: MunicipioModel, request: Request,
                           user: dict = Depends(_require_manage)):
    db = _db(request); uid = _uid(user)
    if await db.tasas_municipios.find_one({"user_id": uid, "codigo": payload.codigo}):
        raise HTTPException(400, "Ya existe un municipio con ese código")
    doc = payload.model_dump()
    doc["user_id"] = uid
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    await db.tasas_municipios.insert_one(doc)
    return payload.model_dump()


@router.put("/municipios/{codigo}")
async def update_municipio(codigo: str, payload: MunicipioModel, request: Request,
                           user: dict = Depends(_require_manage)):
    if payload.codigo != codigo:
        raise HTTPException(400, "No se puede cambiar el código")
    db = _db(request); uid = _uid(user)
    res = await db.tasas_municipios.update_one(
        {"user_id": uid, "codigo": codigo}, {"$set": payload.model_dump()})
    if res.matched_count == 0:
        raise HTTPException(404, "Municipio no encontrado")
    return payload.model_dump()


@router.delete("/municipios/{codigo}")
async def delete_municipio(codigo: str, request: Request,
                           user: dict = Depends(_require_manage)):
    db = _db(request); uid = _uid(user)
    res = await db.tasas_municipios.delete_one({"user_id": uid, "codigo": codigo})
    if res.deleted_count == 0:
        raise HTTPException(404, "Municipio no encontrado")
    return {"ok": True}


# =============================================================================
# Tasas: upload + generate
# =============================================================================
async def _process_upload(db, uid: str, filename: str, raw: bytes, source: str | None = None) -> dict:
    try:
        rows = parse_csv_rows(raw)
    except Exception as e:
        raise HTTPException(400, f"CSV inválido: {e}")
    if not rows:
        raise HTTPException(400, "El CSV no contiene filas válidas")

    upload_id = str(uuid.uuid4())
    user_dir = JOBS_DIR / uid / "tasas_uploads"
    user_dir.mkdir(parents=True, exist_ok=True)
    csv_path = user_dir / f"{upload_id}.csv"
    csv_path.write_bytes(raw)

    grouped = aggregate_by_municipio(rows)
    user_munis = {m["codigo"]: m async for m in db.tasas_municipios.find(
        {"user_id": uid},
        {"codigo": 1, "nombre": 1, "calle": 1, "numero": 1, "codigo_postal": 1,
         "provincia": 1, "telefono_contacto": 1, "persona_contacto": 1})}

    detected = []
    for codigo, m in grouped.items():
        existing = user_munis.get(codigo)
        detected.append({
            "codigo": codigo,
            "nombre": (existing or {}).get("nombre", f"AYUNTAMIENTO {codigo}"),
            "exists_in_crud": existing is not None,
            "rows": len(m["rows"]),
            "total_tasa": round(m["total_tasa"], 2),
            "min_period": f"{m['min_period'][0]:04d}-{m['min_period'][1]:02d}" if m["min_period"] else "",
            "max_period": f"{m['max_period'][0]:04d}-{m['max_period'][1]:02d}" if m["max_period"] else "",
        })
    detected.sort(key=lambda x: x["codigo"])

    doc = {
        "_id": upload_id, "user_id": uid, "filename": filename,
        "path": str(csv_path), "row_count": len(rows),
        "municipios_count": len(grouped),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if source:
        doc["source"] = source
    await db.tasas_uploads.insert_one(doc)
    return {"id": upload_id, "filename": filename, "row_count": len(rows),
            "municipios_count": len(grouped), "municipios": detected,
            **({"source": source} if source else {})}


@router.post("/upload")
async def tasas_upload(request: Request, file: UploadFile = File(...),
                       user: dict = Depends(_require_manage)):
    name = (file.filename or "").lower()
    if not (name.endswith(".csv") or name.endswith(".txt")):
        raise HTTPException(400, "El archivo debe ser CSV (separador ;)")
    raw = await file.read()
    return await _process_upload(_db(request), _uid(user), file.filename or "tasas.csv", raw)


@router.post("/generate")
async def tasas_generate(payload: TasasGenerateRequest, request: Request,
                         user: dict = Depends(_require_manage)):
    db = _db(request); uid = _uid(user)
    upload = await db.tasas_uploads.find_one({"_id": payload.upload_id, "user_id": uid})
    if not upload:
        raise HTTPException(404, "Subida no encontrada")
    csv_path = Path(upload["path"])
    if not csv_path.exists():
        raise HTTPException(404, "Archivo CSV no disponible")

    rows = parse_csv_rows(csv_path.read_bytes())
    grouped = aggregate_by_municipio(rows)
    selected = set(payload.codigos) if payload.codigos else set(grouped.keys())

    user_munis = {m["codigo"]: m async for m in db.tasas_municipios.find(
        {"user_id": uid},
        {"codigo": 1, "nombre": 1, "calle": 1, "numero": 1, "codigo_postal": 1,
         "provincia": 1, "telefono_contacto": 1, "persona_contacto": 1})}

    job_id = str(uuid.uuid4())
    output_dir = JOBS_DIR / uid / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    app_settings = await db.tasas_settings.find_one({"_id": "sharepoint"}) or {}
    sp_client = None
    sharepoint_uploads: List[Dict[str, Any]] = []
    if payload.upload_to_sharepoint:
        if not app_settings.get("enabled_output"):
            raise HTTPException(400, "La subida a SharePoint no está habilitada. Configúrala en Ajustes.")
        sp_client = get_sp_client(app_settings)

    files, errors = [], []
    auto_created = 0
    for codigo, m in grouped.items():
        if codigo not in selected:
            continue
        municipio = user_munis.get(codigo)
        if not municipio:
            municipio = {
                "codigo": codigo, "nombre": f"AYUNTAMIENTO {codigo}",
                "calle": "", "numero": "", "codigo_postal": "",
                "provincia": "", "telefono_contacto": "", "persona_contacto": "",
                "user_id": uid, "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.tasas_municipios.insert_one(municipio.copy())
            auto_created += 1
        muni_for_pdf = {**municipio, "min_period": m["min_period"],
                        "max_period": m["max_period"], "total_tasa": m["total_tasa"]}
        try:
            sociedad = m["rows"][0]["sociedad"] if m["rows"] else ""
            pdf_bytes = build_tasas_pdf(
                muni_for_pdf, m["rows"],
                atencion_telefono=app_settings.get("atencion_telefono", "900 907 000"),
                sociedad=sociedad,
                logos_by_sociedad=app_settings.get("logos_by_sociedad") or {},
            )
            fname = safe_filename(f"Tasas_{municipio['nombre']}_{codigo}") + ".pdf"
            (output_dir / fname).write_bytes(pdf_bytes)
            files.append(fname)
            if sp_client:
                sp_fname = filename_for_output(municipio["nombre"])
                uploaded = sp_client.upload_output(municipio["nombre"], sp_fname, pdf_bytes)
                sharepoint_uploads.append({
                    "codigo": codigo, "municipio": municipio["nombre"],
                    "filename": sp_fname,
                    "path": uploaded.get("path"), "web_url": uploaded.get("web_url"),
                })
        except Exception as e:
            errors.append({"codigo": codigo, "error": str(e)})

    zip_path = output_dir / "_all.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in files:
            zf.write(output_dir / fn, fn)

    job_doc = {
        "_id": job_id, "user_id": uid, "type": "tasas",
        "template_name": "Tasas Eléctricas / Gas",
        "excel_filename": upload["filename"],
        "row_count": len(selected),
        "generated_count": len(files), "error_count": len(errors),
        "auto_created_municipios": auto_created, "errors": errors,
        "files": files, "sharepoint_uploads": sharepoint_uploads,
        "status": "completado" if not errors else ("parcial" if files else "fallido"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.tasas_jobs.insert_one(job_doc)
    return {"id": job_id, "status": job_doc["status"],
            "generated_count": len(files), "error_count": len(errors),
            "auto_created_municipios": auto_created, "files": files,
            "sharepoint_uploads": sharepoint_uploads}


# =============================================================================
# Jobs
# =============================================================================
@router.get("/jobs")
async def list_jobs(request: Request, user: dict = Depends(_require_view)):
    db = _db(request); uid = _uid(user)
    cursor = db.tasas_jobs.find({"user_id": uid}, {
        "_id": 1, "template_name": 1, "excel_filename": 1, "row_count": 1,
        "generated_count": 1, "error_count": 1, "status": 1, "created_at": 1,
    }).sort("created_at", -1).limit(200)
    return [{"id": j["_id"], **{k: j.get(k, "") for k in
            ["template_name", "excel_filename", "row_count", "generated_count",
             "error_count", "status", "created_at"]}} async for j in cursor]


@router.get("/jobs/auth/download-token")
async def download_token(user: dict = Depends(_require_view)):
    return {"token": create_access_token(_uid(user), user.get("email", ""))}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, user: dict = Depends(_require_view)):
    db = _db(request); uid = _uid(user)
    j = await db.tasas_jobs.find_one({"_id": job_id, "user_id": uid})
    if not j:
        raise HTTPException(404, "Trabajo no encontrado")
    return {"id": j["_id"], **{k: j.get(k, "") for k in
            ["template_name", "excel_filename", "row_count", "generated_count",
             "error_count", "errors", "files", "sharepoint_uploads",
             "status", "created_at"]}}


def _resolve_uid_with_token(request: Request, token: Optional[str]) -> str:
    if token:
        payload = decode_token(token, "access")
        return payload["sub"]
    cookie = request.cookies.get(COOKIE_ACCESS)
    if not cookie:
        raise HTTPException(401, "No autenticado")
    payload = decode_token(cookie, "access")
    return payload["sub"]


@router.get("/jobs/{job_id}/download")
async def download_zip(job_id: str, request: Request, token: Optional[str] = None):
    uid = _resolve_uid_with_token(request, token)
    db = _db(request)
    j = await db.tasas_jobs.find_one({"_id": job_id, "user_id": uid})
    if not j:
        raise HTTPException(404, "Trabajo no encontrado")
    zip_path = JOBS_DIR / uid / job_id / "_all.zip"
    if not zip_path.exists():
        raise HTTPException(404, "ZIP no encontrado")
    return FileResponse(str(zip_path), media_type="application/zip",
                        filename=f"{j.get('template_name','documentos')}.zip")


@router.get("/jobs/{job_id}/files/{filename}")
async def download_file(job_id: str, filename: str, request: Request,
                        token: Optional[str] = None):
    uid = _resolve_uid_with_token(request, token)
    db = _db(request)
    j = await db.tasas_jobs.find_one({"_id": job_id, "user_id": uid})
    if not j:
        raise HTTPException(404, "Trabajo no encontrado")
    file_path = JOBS_DIR / uid / job_id / filename
    if not file_path.exists() or not str(file_path.resolve()).startswith(str(JOBS_DIR.resolve())):
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(str(file_path), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{safe_filename(filename)}"'})


# =============================================================================
# Settings (admin)
# =============================================================================
@router.get("/settings", response_model=SharepointSettings)
async def get_settings(request: Request, _: dict = Depends(_require_admin)):
    db = _db(request)
    doc = await db.tasas_settings.find_one({"_id": "sharepoint"}) or {}
    return SharepointSettings(
        enabled_input=doc.get("enabled_input", False),
        enabled_output=doc.get("enabled_output", False),
        mock_mode=doc.get("mock_mode", True),
        tenant_id=doc.get("tenant_id", ""), client_id=doc.get("client_id", ""),
        client_secret="***" if doc.get("client_secret") else "",
        site_url=doc.get("site_url", ""),
        input_folder=doc.get("input_folder", "/Tasas/Entrada"),
        output_folder=doc.get("output_folder", "/Tasas/Salida"),
        atencion_telefono=doc.get("atencion_telefono", "900 907 000"),
        logos_by_sociedad=doc.get("logos_by_sociedad", {}),
    )


@router.put("/settings", response_model=SharepointSettings)
async def put_settings(payload: SharepointSettings, request: Request,
                       admin: dict = Depends(_require_admin)):
    db = _db(request)
    existing = await db.tasas_settings.find_one({"_id": "sharepoint"}) or {}
    update = payload.model_dump()
    if update.get("client_secret") in ("", "***"):
        update["client_secret"] = existing.get("client_secret", "")
    update["_id"] = "sharepoint"
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    update["updated_by"] = admin.get("email", "")
    await db.tasas_settings.update_one({"_id": "sharepoint"}, {"$set": update}, upsert=True)
    payload.client_secret = "***" if update["client_secret"] else ""
    return payload


@router.get("/settings/public")
async def get_settings_public(request: Request, _: dict = Depends(_require_view)):
    db = _db(request)
    doc = await db.tasas_settings.find_one({"_id": "sharepoint"}) or {}
    return {"enabled_input": doc.get("enabled_input", False),
            "enabled_output": doc.get("enabled_output", False),
            "mock_mode": doc.get("mock_mode", True),
            "input_folder": doc.get("input_folder", ""),
            "output_folder": doc.get("output_folder", ""),
            "atencion_telefono": doc.get("atencion_telefono", "900 907 000")}


@router.get("/sharepoint/input-files")
async def sharepoint_list_input(request: Request, user: dict = Depends(_require_manage)):
    db = _db(request)
    settings = await db.tasas_settings.find_one({"_id": "sharepoint"}) or {}
    if not settings.get("enabled_input"):
        raise HTTPException(400, "La importación desde SharePoint no está habilitada")
    client = get_sp_client(settings)
    return {"files": client.list_input_files(), "mock_mode": settings.get("mock_mode", True)}


@router.post("/upload-from-sharepoint")
async def tasas_upload_from_sharepoint(payload: SharepointImportRequest, request: Request,
                                       user: dict = Depends(_require_manage)):
    db = _db(request); uid = _uid(user)
    settings = await db.tasas_settings.find_one({"_id": "sharepoint"}) or {}
    if not settings.get("enabled_input"):
        raise HTTPException(400, "La importación desde SharePoint no está habilitada")
    client = get_sp_client(settings)
    try:
        filename, raw = client.read_input_file(payload.file_id)
    except FileNotFoundError:
        raise HTTPException(404, "Archivo no encontrado en SharePoint")
    return await _process_upload(db, uid, filename, raw, source="sharepoint")
