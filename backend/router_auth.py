"""
router_auth.py — Endpoints públicos de autenticación

Rutas (todas bajo /api/auth):
  POST /login                       — credenciales → cookies HTTP-only + user
  POST /logout                      — borra cookies
  GET  /me                          — usuario actual (autenticado)
  POST /refresh                     — refresca access token desde cookie refresh
  GET  /setup/{token}/check         — comprueba validez del token de alta
  POST /setup/{token}               — define contraseña por primera vez
  POST /forgot-password             — solicita reset
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr, Field

from auth import (
    COOKIE_REFRESH,
    LOCKOUT_MINUTES,
    LOCKOUT_THRESHOLD,
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_setup_token,
    get_current_user,
    hash_password,
    is_locked_out,
    register_failed_attempt,
    reset_attempts,
    set_auth_cookies,
    setup_token_expiry,
    verify_password,
)
from email_service import enviar_email_setup_password

router = APIRouter(prefix="/auth", tags=["auth"])


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class SetupPasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class ForgotPasswordIn(BaseModel):
    email: EmailStr


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _db(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.mongo_db


def _to_safe(user: dict) -> dict:
    """Quita campos sensibles antes de devolver el usuario al cliente."""
    safe = dict(user)
    safe.pop("password_hash", None)
    return safe


def _client_ip(request: Request) -> str:
    h = request.headers
    return (
        h.get("x-forwarded-for", "").split(",")[0].strip()
        or h.get("x-real-ip", "")
        or (request.client.host if request.client else "unknown")
    )


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.post("/login")
async def login(payload: LoginIn, request: Request, response: Response):
    db = _db(request)
    email = payload.email.lower().strip()
    identifier = f"{_client_ip(request)}:{email}"

    if await is_locked_out(db, identifier):
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados intentos fallidos. Vuelve en {LOCKOUT_MINUTES} min.",
        )

    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("password_hash") or ""):
        await register_failed_attempt(db, identifier)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if user.get("status") != "active":
        raise HTTPException(
            status_code=403,
            detail=f"La cuenta está en estado '{user.get('status', 'desconocido')}'. "
                   "Si te acaban de invitar, abre el enlace de activación que recibiste por email.",
        )

    await reset_attempts(db, identifier)
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"last_login": datetime.now(timezone.utc).isoformat()}},
    )

    access = create_access_token(user["_id"], email)
    refresh = create_refresh_token(user["_id"])
    set_auth_cookies(response, access, refresh)

    # Cargamos permisos del rol para devolverlos en la respuesta
    role = await db.roles.find_one({"name": user.get("role")}) if user.get("role") else None
    user_out = _to_safe(user)
    user_out["permisos"] = sorted((role or {}).get("permissions", []))
    return user_out


@router.post("/logout")
async def logout(response: Response):
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get(COOKIE_REFRESH)
    if not token:
        raise HTTPException(status_code=401, detail="Sin refresh token")
    payload = decode_token(token, "refresh")
    db = _db(request)
    user = await db.users.find_one({"_id": payload["sub"]})
    if not user or user.get("status") != "active":
        raise HTTPException(status_code=401, detail="Usuario no válido")
    new_access = create_access_token(user["_id"], user["email"])
    # Reemitimos también el refresh para extender la sesión activa.
    new_refresh = create_refresh_token(user["_id"])
    set_auth_cookies(response, new_access, new_refresh)
    return {"ok": True}


@router.get("/setup/{token}/check")
async def setup_check(token: str, request: Request):
    db = _db(request)
    rec = await db.activation_tokens.find_one({"token": token})
    if not rec:
        raise HTTPException(404, "Enlace no válido")
    if rec.get("used"):
        raise HTTPException(410, "Enlace ya utilizado")
    exp = rec.get("expires_at")
    if isinstance(exp, str):
        try:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        except ValueError:
            exp_dt = None
    else:
        exp_dt = exp
    if exp_dt and exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    if not exp_dt or exp_dt < datetime.now(timezone.utc):
        raise HTTPException(410, "Enlace expirado")
    # Devuelve email y nombre para mostrar en la UI
    user = await db.users.find_one({"_id": rec["user_id"]})
    if not user:
        raise HTTPException(404, "Usuario asociado no existe")
    return {"email": user.get("email"), "name": user.get("name"), "motivo": rec.get("motivo", "alta")}


@router.post("/setup/{token}")
async def setup(token: str, payload: SetupPasswordIn, request: Request, response: Response):
    db = _db(request)
    rec = await db.activation_tokens.find_one({"token": token})
    if not rec or rec.get("used"):
        raise HTTPException(410, "Enlace no válido o ya utilizado")
    exp = rec.get("expires_at")
    if isinstance(exp, str):
        try:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        except ValueError:
            exp_dt = None
    else:
        exp_dt = exp
    if exp_dt and exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    if not exp_dt or exp_dt < datetime.now(timezone.utc):
        raise HTTPException(410, "Enlace expirado")

    user = await db.users.find_one({"_id": rec["user_id"]})
    if not user:
        raise HTTPException(404, "Usuario no existe")

    # Actualiza password y activa la cuenta
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "password_hash": hash_password(payload.password),
            "status": "active",
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    await db.activation_tokens.update_one(
        {"_id": rec["_id"]},
        {"$set": {"used": True, "used_at": datetime.now(timezone.utc).isoformat()}},
    )

    # Auto-login tras setup
    access = create_access_token(user["_id"], user["email"])
    refresh = create_refresh_token(user["_id"])
    set_auth_cookies(response, access, refresh)
    role = await db.roles.find_one({"name": user.get("role")}) if user.get("role") else None
    out = _to_safe({**user, "status": "active"})
    out["permisos"] = sorted((role or {}).get("permissions", []))
    return out


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordIn, request: Request):
    """Genera un token de reset y lo envía por email. Por seguridad nunca
    revela si el email existe (siempre responde 200)."""
    db = _db(request)
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if user and user.get("status") in ("active", "pending"):
        token = generate_setup_token()
        await db.activation_tokens.insert_one({
            "_id": token,
            "token": token,
            "user_id": user["_id"],
            "motivo": "reset",
            "expires_at": setup_token_expiry().isoformat(),
            "used": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await enviar_email_setup_password(
            to=email,
            nombre=user.get("name", ""),
            token=token,
            motivo="reset",
        )
    # Respuesta uniforme
    return {"ok": True}
