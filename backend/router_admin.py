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
from typing import Optional

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
    {"key": "users.manage", "label": "Gestionar usuarios"},
    {"key": "roles.manage", "label": "Gestionar roles y permisos"},
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
