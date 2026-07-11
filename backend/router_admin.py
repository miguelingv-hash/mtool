"""
router_admin.py — Endpoints de administración de usuarios y roles

Todos requieren permiso correspondiente. El usuario con rol 'admin' (que tiene
el wildcard '*') puede usar cualquiera de ellos.

Rutas (bajo /api/admin):
  GET    /users                  — lista usuarios
  POST   /users                  — invita usuario (crea pending + envía email)
  PATCH  /users/{id}             — modifica rol o estado
  DELETE /users/{id}             — elimina usuario
  POST   /users/{id}/resend      — reenvía el correo de activación
  GET    /roles                  — lista roles
  POST   /roles                  — crea rol
  PATCH  /roles/{name}           — modifica permisos del rol
  DELETE /roles/{name}           — borra rol (no se permite borrar 'admin')
  GET    /permissions/catalog    — catálogo de permisos disponibles
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr, Field

from auth import (
    generate_setup_token,
    require_permission,
    setup_token_expiry,
)
from email_service import enviar_email_setup_password

router = APIRouter(prefix="/admin", tags=["admin"])


# Catálogo central de permisos (lo usa la UI de roles para mostrar checkboxes).
# Crece junto a la app sin hacer falta migraciones — los roles existentes
# guardan strings que pueden o no estar aquí.
PERMISSIONS_CATALOG = [
    {"key": "*", "label": "Acceso total (wildcard de admin)"},
    {"key": "consultas.unitaria", "label": "Consultar factura unitaria SII"},
    {"key": "consultas.batch", "label": "Consultar lote CSV (batch)"},
    {"key": "consultas.mensual", "label": "Lanzar jobs mensuales SII"},
    {"key": "comparativa.view", "label": "Ver comparativa SII ↔ Comercial"},
    {"key": "comparativa.edit_config", "label": "Editar configuración de comparativa"},
    {"key": "comercial.import", "label": "Importar CSV comercial (SAP/SIGLO)"},
    {"key": "conciliacion.view", "label": "Ver/usar conciliación Newman"},
    {"key": "conciliacion.import", "label": "Importar faltantes en BD"},
    {"key": "logs.view", "label": "Ver log de Web Services"},
    {"key": "tasas.view", "label": "Ver Tasas Municipales (panel + jobs + municipios)"},
    {"key": "tasas.manage", "label": "Generar PDFs de Tasas + gestionar municipios"},
    {"key": "tasas.admin", "label": "Ajustes globales de Tasas (SharePoint, etc.)"},
    {"key": "users.manage", "label": "Gestionar usuarios"},
    {"key": "roles.manage", "label": "Gestionar roles y permisos"},
    {"key": "sii.wipe", "label": "Vaciar módulo SII (operación destructiva)"},
    {"key": "audit.view", "label": "Ver historial de importaciones (audit trail)"},
]


def _db(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.mongo_db


def _safe_user(u: dict) -> dict:
    out = dict(u)
    out.pop("password_hash", None)
    return out


# ============================================================================
# Schemas
# ============================================================================
class InviteUserIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=60)


class PatchUserIn(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|pending|disabled)$")
    name: Optional[str] = Field(None, min_length=1, max_length=120)


class RoleIn(BaseModel):
    name: str = Field(min_length=2, max_length=60, pattern=r"^[a-z0-9_-]+$")
    description: str = Field("", max_length=200)
    permissions: list[str] = Field(default_factory=list)


class PatchRoleIn(BaseModel):
    description: Optional[str] = Field(None, max_length=200)
    permissions: Optional[list[str]] = None


# ============================================================================
# Users
# ============================================================================
@router.get("/users")
async def list_users(
    request: Request,
    _: dict = Depends(require_permission("users.manage")),
):
    db = _db(request)
    cursor = db.users.find({}, {"password_hash": 0}).sort("created_at", -1)
    users = []
    async for u in cursor:
        users.append(u)
    return users


@router.post("/users")
async def invite_user(
    payload: InviteUserIn,
    request: Request,
    _: dict = Depends(require_permission("users.manage")),
):
    db = _db(request)
    email = payload.email.lower().strip()

    # El rol debe existir
    role = await db.roles.find_one({"name": payload.role})
    if not role:
        raise HTTPException(400, f"El rol '{payload.role}' no existe")

    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(409, "Ya existe un usuario con ese email")

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.users.insert_one({
        "_id": user_id,
        "email": email,
        "name": payload.name,
        "role": payload.role,
        "status": "pending",
        "password_hash": None,
        "created_at": now,
        "invited_by": "admin",  # se podría sustituir por el email del invitador
    })

    token = generate_setup_token()
    await db.activation_tokens.insert_one({
        "_id": token,
        "token": token,
        "user_id": user_id,
        "motivo": "alta",
        "expires_at": setup_token_expiry().isoformat(),
        "used": False,
        "created_at": now,
    })

    res = await enviar_email_setup_password(
        to=email, nombre=payload.name, token=token, motivo="alta",
    )
    return {
        "user": await db.users.find_one({"_id": user_id}, {"password_hash": 0}),
        "activation_link_status": res.get("status"),
        "activation_token": token if res.get("status") != "sent" else None,
    }


@router.post("/users/{user_id}/resend")
async def resend_invite(
    user_id: str,
    request: Request,
    _: dict = Depends(require_permission("users.manage")),
):
    db = _db(request)
    user = await db.users.find_one({"_id": user_id})
    if not user:
        raise HTTPException(404, "Usuario no existe")
    if user.get("status") == "active":
        raise HTTPException(400, "El usuario ya está activo")

    # Invalida tokens anteriores y emite uno nuevo
    await db.activation_tokens.update_many(
        {"user_id": user_id, "used": False},
        {"$set": {"used": True, "invalidated_at": datetime.now(timezone.utc).isoformat()}},
    )
    token = generate_setup_token()
    await db.activation_tokens.insert_one({
        "_id": token,
        "token": token,
        "user_id": user_id,
        "motivo": "alta",
        "expires_at": setup_token_expiry().isoformat(),
        "used": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    res = await enviar_email_setup_password(
        to=user["email"], nombre=user.get("name", ""), token=token, motivo="alta",
    )
    return {
        "ok": True,
        "activation_link_status": res.get("status"),
        "activation_token": token if res.get("status") != "sent" else None,
    }


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    payload: PatchUserIn,
    request: Request,
    actor: dict = Depends(require_permission("users.manage")),
):
    db = _db(request)
    user = await db.users.find_one({"_id": user_id})
    if not user:
        raise HTTPException(404, "Usuario no existe")
    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if "role" in update:
        if not await db.roles.find_one({"name": update["role"]}):
            raise HTTPException(400, f"El rol '{update['role']}' no existe")
    if not update:
        raise HTTPException(400, "Sin cambios")
    # Anti-foot-shoot: el admin no puede desactivarse a sí mismo ni quitarse el rol admin
    if user_id == actor.get("_id"):
        if update.get("status") and update["status"] != "active":
            raise HTTPException(400, "No puedes desactivar tu propia cuenta")
        if update.get("role") and update["role"] != "admin":
            raise HTTPException(400, "No puedes quitarte el rol admin a ti mismo")
    await db.users.update_one({"_id": user_id}, {"$set": update})
    return await db.users.find_one({"_id": user_id}, {"password_hash": 0})


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    actor: dict = Depends(require_permission("users.manage")),
):
    if user_id == actor.get("_id"):
        raise HTTPException(400, "No puedes eliminar tu propia cuenta")
    db = _db(request)
    res = await db.users.delete_one({"_id": user_id})
    if res.deleted_count == 0:
        raise HTTPException(404, "Usuario no existe")
    await db.activation_tokens.delete_many({"user_id": user_id})
    return {"ok": True}


# ============================================================================
# Roles
# ============================================================================
@router.get("/roles")
async def list_roles(
    request: Request,
    _: dict = Depends(require_permission("roles.manage")),
):
    db = _db(request)
    return [r async for r in db.roles.find({}).sort("name", 1)]


@router.post("/roles")
async def create_role(
    payload: RoleIn,
    request: Request,
    _: dict = Depends(require_permission("roles.manage")),
):
    db = _db(request)
    if await db.roles.find_one({"name": payload.name}):
        raise HTTPException(409, "Ya existe un rol con ese nombre")
    doc = {
        "_id": payload.name,
        "name": payload.name,
        "description": payload.description,
        "permissions": payload.permissions,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.roles.insert_one(doc)
    return doc


@router.patch("/roles/{name}")
async def patch_role(
    name: str,
    payload: PatchRoleIn,
    request: Request,
    _: dict = Depends(require_permission("roles.manage")),
):
    db = _db(request)
    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(400, "Sin cambios")
    # No permitir quitar '*' del rol admin
    if name == "admin" and "permissions" in update and "*" not in update["permissions"]:
        update["permissions"] = ["*"] + [p for p in update["permissions"] if p != "*"]
    res = await db.roles.update_one({"name": name}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "Rol no existe")
    return await db.roles.find_one({"name": name})


@router.delete("/roles/{name}")
async def delete_role(
    name: str,
    request: Request,
    _: dict = Depends(require_permission("roles.manage")),
):
    if name == "admin":
        raise HTTPException(400, "No se puede borrar el rol 'admin'")
    db = _db(request)
    # No permitir borrar un rol que esté en uso
    asignados = await db.users.count_documents({"role": name})
    if asignados:
        raise HTTPException(400, f"El rol está asignado a {asignados} usuarios")
    res = await db.roles.delete_one({"name": name})
    if res.deleted_count == 0:
        raise HTTPException(404, "Rol no existe")
    return {"ok": True}


@router.get("/permissions/catalog")
async def permissions_catalog(
    _: dict = Depends(require_permission("roles.manage")),
):
    return PERMISSIONS_CATALOG


# ============================================================================
# Mantenimiento del módulo SII (destructivo)
# ============================================================================
SII_WIPE_COLLECTIONS = (
    "facturas_sii",
    "facturas_comercial",
    "consultas",
    "jobs",
)

# Selección granular de qué colecciones se vacían según `scope`:
#   - "todo"      → SII + Comercial + consultas SOAP + jobs async
#   - "sii"       → sólo facturas_sii + consultas (el log SOAP es SII-only)
#   - "comercial" → sólo facturas_comercial (SAP FI + SIGLO viven aquí)
SII_WIPE_SCOPES: dict[str, tuple[str, ...]] = {
    "todo": ("facturas_sii", "facturas_comercial", "consultas", "jobs"),
    "sii": ("facturas_sii", "consultas"),
    "comercial": ("facturas_comercial",),
}


class WipeSIIIn(BaseModel):
    confirmacion: str = Field(
        ...,
        description="Debe ser exactamente 'VACIAR' para confirmar la operación.",
    )
    scope: Literal["todo", "sii", "comercial"] = Field(
        default="todo",
        description=(
            "Ámbito a vaciar. `todo`: SII + Comercial + logs + jobs. "
            "`sii`: sólo facturas_sii + log de consultas SOAP. "
            "`comercial`: sólo facturas_comercial (SAP FI + SIGLO)."
        ),
    )


@router.post("/sii/vaciar-modulo")
async def vaciar_modulo_sii(
    request: Request,
    payload: WipeSIIIn,
    dry_run: bool = False,
    _: dict = Depends(require_permission("sii.wipe")),
):
    """Vacía colecciones del módulo SII de forma selectiva según `scope`.

    Scopes soportados:
      - `todo`:      `facturas_sii`, `facturas_comercial`, `consultas`, `jobs`
      - `sii`:       `facturas_sii`, `consultas`  (las consultas SOAP son SII)
      - `comercial`: `facturas_comercial`         (SAP FI + SIGLO)

    No toca nunca: `users`, `roles`, `comparativa_config`, `sociedades_catalogo`,
    `tasas_*`, `users_mfa`, `login_attempts`, `activation_tokens`.

    Requisitos:
      - Permiso `sii.wipe` (solo admin por defecto).
      - Body con `confirmacion = "VACIAR"`.
      - `dry_run=true` (query param) para sólo contar sin borrar.
    """
    if payload.confirmacion != "VACIAR":
        raise HTTPException(
            400,
            "Confirmación inválida. El campo 'confirmacion' debe ser exactamente 'VACIAR'.",
        )
    collections = SII_WIPE_SCOPES.get(payload.scope)
    if not collections:
        raise HTTPException(400, f"Scope inválido: {payload.scope!r}")

    db = _db(request)
    resumen: dict[str, dict[str, int]] = {}
    for col in collections:
        antes = await db[col].count_documents({})
        if dry_run:
            resumen[col] = {"antes": antes, "borrados": 0, "despues": antes}
        else:
            res = await db[col].delete_many({})
            despues = await db[col].count_documents({})
            resumen[col] = {
                "antes": antes,
                "borrados": res.deleted_count,
                "despues": despues,
            }
    return {
        "dry_run": dry_run,
        "scope": payload.scope,
        "colecciones_afectadas": list(collections),
        "resumen": resumen,
    }



# ============================================================================
# Catálogo de Sociedades (Soc. → NIF titular + nombre)
# ============================================================================

# Default seed mirrored from router_facturas._SOCIEDADES_DEFAULT. Lo
# duplicamos aquí para no acoplar imports — si cambia uno hay que actualizar
# el otro (los tests cubren la coherencia mínima).
_SOCIEDADES_SEED: dict[str, dict] = {
    "4432": {"nif_titular": "A95000295", "nombre_titular": "TotalEnergies Clientes S.A.U."},
    "2239": {"nif_titular": "A74251836", "nombre_titular": "BASER"},
}


class SociedadIn(BaseModel):
    nif_titular: str = Field(..., min_length=1, max_length=20)
    nombre_titular: str = Field(default="", max_length=200)


class SociedadesPutIn(BaseModel):
    entries: dict[str, SociedadIn] = Field(
        default_factory=dict,
        description="Mapa {soc: {nif_titular, nombre_titular}}. Reemplaza por completo los overrides existentes.",
    )


@router.get("/sociedades")
async def get_sociedades(
    request: Request,
    _: dict = Depends(require_permission("sii.wipe")),
):
    """Devuelve el catálogo Soc → NIF/Nombre.

    Combina los `_SOCIEDADES_SEED` (defaults cableados) con los overrides
    persistidos en `sociedades_catalogo._id="default".entries`.
    """
    db = _db(request)
    doc = await db.sociedades_catalogo.find_one({"_id": "default"}) or {}
    persisted = doc.get("entries") or {}
    merged: dict[str, dict] = {}
    for soc, info in _SOCIEDADES_SEED.items():
        merged[str(soc)] = dict(info)
    for soc, info in persisted.items():
        if isinstance(info, dict) and info.get("nif_titular"):
            merged[str(soc)] = {
                "nif_titular": str(info["nif_titular"]).strip().upper(),
                "nombre_titular": str(info.get("nombre_titular") or "").strip(),
            }
    return {
        "sociedades": merged,
        "seed": _SOCIEDADES_SEED,
        "persisted": persisted,
    }


@router.put("/sociedades")
async def put_sociedades(
    request: Request,
    payload: SociedadesPutIn,
    _: dict = Depends(require_permission("sii.wipe")),
):
    """Reemplaza por completo los overrides persistidos del catálogo de
    sociedades. Los seeds cableados se mantienen — para "anular" un seed,
    aporta un override con el mismo `soc` y el `nif_titular` correcto.
    """
    db = _db(request)
    entries_norm: dict[str, dict] = {}
    for soc, info in payload.entries.items():
        soc_clean = str(soc).strip()
        if not soc_clean:
            continue
        entries_norm[soc_clean] = {
            "nif_titular": info.nif_titular.strip().upper(),
            "nombre_titular": (info.nombre_titular or "").strip(),
        }
    await db.sociedades_catalogo.update_one(
        {"_id": "default"},
        {"$set": {
            "entries": entries_norm,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {"ok": True, "count": len(entries_norm), "entries": entries_norm}


class BackfillNifPorSocIn(BaseModel):
    dry_run: bool = False
    # Modo "asignación masiva": ignora `soc_origen` y asigna TODOS los docs
    # comerciales sin `nif_titular` al NIF aportado. Útil para data legacy
    # que se cargó antes de que el parser leyera el campo `Soc.`.
    fallback_nif_titular: Optional[str] = None
    fallback_nombre_titular: Optional[str] = None


@router.post("/comercial/asignar-nif-titular-por-soc")
async def backfill_nif_titular_por_soc(
    request: Request,
    payload: BackfillNifPorSocIn,
    _: dict = Depends(require_permission("sii.wipe")),
):
    """Asigna `nif_titular` + `nombre_titular` a los docs de
    `facturas_comercial` que aún no lo tienen.

    Estrategia:
      1) Match por `soc_origen` contra el catálogo (recomendado, requiere
         que los docs hayan sido cargados con el parser nuevo que captura
         `Soc.`).
      2) Si `fallback_nif_titular` está informado, los docs sin
         `soc_origen` (o con `soc_origen` no mapeable) se asignan al NIF
         de fallback. Útil para la migración inicial.

    Si `dry_run=true`, sólo cuenta sin escribir.
    """
    db = _db(request)
    doc = await db.sociedades_catalogo.find_one({"_id": "default"}) or {}
    persisted = doc.get("entries") or {}
    catalogo: dict[str, dict] = {}
    for soc, info in _SOCIEDADES_SEED.items():
        catalogo[str(soc)] = dict(info)
    for soc, info in persisted.items():
        if isinstance(info, dict) and info.get("nif_titular"):
            catalogo[str(soc)] = {
                "nif_titular": str(info["nif_titular"]).strip().upper(),
                "nombre_titular": str(info.get("nombre_titular") or "").strip(),
            }

    sin_nif_filter = {"$or": [
        {"nif_titular": None},
        {"nif_titular": ""},
        {"nif_titular": {"$exists": False}},
    ]}

    resumen: dict[str, int] = {"por_soc": 0, "fallback": 0, "sin_asignar": 0}
    detalle_por_soc: dict[str, int] = {}

    # 1) Match por soc_origen
    for soc, info in catalogo.items():
        flt = {**sin_nif_filter, "soc_origen": soc}
        count = await db.facturas_comercial.count_documents(flt)
        if count == 0:
            continue
        detalle_por_soc[soc] = count
        resumen["por_soc"] += count
        if not payload.dry_run:
            await db.facturas_comercial.update_many(
                flt,
                {"$set": {
                    "nif_titular": info["nif_titular"],
                    "nombre_titular": info["nombre_titular"],
                }},
            )

    # 2) Fallback para los que sigan sin nif_titular
    if payload.fallback_nif_titular:
        nif_fb = payload.fallback_nif_titular.strip().upper()
        nombre_fb = (payload.fallback_nombre_titular or "").strip()
        flt = dict(sin_nif_filter)
        count = await db.facturas_comercial.count_documents(flt)
        resumen["fallback"] = count
        if count > 0 and not payload.dry_run:
            await db.facturas_comercial.update_many(
                flt,
                {"$set": {
                    "nif_titular": nif_fb,
                    "nombre_titular": nombre_fb,
                }},
            )

    # 3) Reporte de los que aún quedarán sin asignar (con dry_run respeta)
    if payload.dry_run:
        # Para dry_run el conteo "sin_asignar" se calcula simulando los
        # updates anteriores: total sin NIF − (asignados por soc) − fallback.
        total_sin_nif = await db.facturas_comercial.count_documents(sin_nif_filter)
        resumen["sin_asignar"] = max(
            0, total_sin_nif - resumen["por_soc"] - resumen["fallback"]
        )
    else:
        resumen["sin_asignar"] = await db.facturas_comercial.count_documents(
            sin_nif_filter
        )

    return {
        "dry_run": payload.dry_run,
        "resumen": resumen,
        "detalle_por_soc": detalle_por_soc,
        "fallback_aplicado": bool(payload.fallback_nif_titular),
    }


# ============================================================================
# Audit Trail — Historial de importaciones (imports_log)
# ============================================================================
@router.get("/imports-log")
async def list_imports_log(
    request: Request,
    skip: int = 0,
    limit: int = 50,
    origen: Optional[str] = None,
    fuente: Optional[str] = None,
    status: Optional[str] = None,
    user_email: Optional[str] = None,
    nif_titular: Optional[str] = None,
    file_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    _: dict = Depends(require_permission("audit.view")),
):
    """Listado paginado del audit trail de importaciones.

    Filtros:
      - `origen`: `sii` | `comercial`
      - `fuente`: `ui_upload` | `cli_newman` | `cli_comercial` | `conciliacion_newman` | `conciliacion_newman_async` | `consulta_mensual_aeat` | `batch_csv`
      - `status`: `running` | `done` | `error`
      - `date_from` / `date_to`: ISO date (YYYY-MM-DD) — filtro por `timestamp_start`
      - `user_email`, `nif_titular`, `file_name`: regex parcial (case-insensitive)
    """
    db = _db(request)
    limit = max(1, min(limit, 200))
    filtro: dict = {}
    if origen:
        filtro["origen"] = origen
    if fuente:
        filtro["fuente"] = fuente
    if status:
        filtro["status"] = status
    if user_email:
        filtro["user_email"] = {"$regex": user_email, "$options": "i"}
    if nif_titular:
        filtro["nif_titular"] = {"$regex": nif_titular, "$options": "i"}
    if file_name:
        filtro["file_name"] = {"$regex": file_name, "$options": "i"}
    if date_from or date_to:
        rango: dict[str, str] = {}
        if date_from:
            rango["$gte"] = date_from
        if date_to:
            rango["$lte"] = (
                f"{date_to}T23:59:59.999999+00:00" if len(date_to) == 10 else date_to
            )
        filtro["timestamp_start"] = rango

    total = await db.imports_log.count_documents(filtro)
    cursor = (
        db.imports_log.find(filtro, {"_id": 0, "errores": 0})
        .sort("timestamp_start", -1)
        .skip(skip)
        .limit(limit)
    )
    items = await cursor.to_list(length=limit)
    return {"total": total, "items": items, "skip": skip, "limit": limit}


@router.get("/imports-log/{import_id}")
async def get_import_log(
    import_id: str,
    request: Request,
    _: dict = Depends(require_permission("audit.view")),
):
    """Detalle completo del audit trail — incluye lista de errores (máx 100)."""
    db = _db(request)
    doc = await db.imports_log.find_one({"id": import_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Registro de importación no encontrado")
    return doc


@router.get("/imports-log/stats/summary")
async def imports_log_stats(
    request: Request,
    _: dict = Depends(require_permission("audit.view")),
):
    """Estadísticas agregadas del audit trail para el dashboard admin."""
    db = _db(request)
    pipeline = [
        {"$group": {
            "_id": {"origen": "$origen", "status": "$status"},
            "count": {"$sum": 1},
            "total_docs": {"$sum": "$total_procesados"},
            "total_insertados": {"$sum": "$insertados"},
        }}
    ]
    agg = await db.imports_log.aggregate(pipeline).to_list(length=100)
    return {"by_origen_status": agg}
