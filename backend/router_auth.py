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
from email_service import enviar_email_setup_password, enviar_email_codigo_mfa
import hmac
import hashlib
import secrets
import uuid
from datetime import timedelta

# MFA config
MFA_CODE_TTL_MIN = 5
MFA_MAX_ATTEMPTS = 3
MFA_RESEND_THROTTLE_SEC = 60

router = APIRouter(prefix="/auth", tags=["auth"])


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class MfaVerifyIn(BaseModel):
    challenge_id: str
    code: str = Field(min_length=4, max_length=10)


class MfaResendIn(BaseModel):
    challenge_id: str


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
def _hash_otp(code: str) -> str:
    """HMAC-SHA256 del OTP con JWT_SECRET como clave. Tiempo-constante en comparación."""
    key = os.environ.get("JWT_SECRET", "").encode("utf-8")
    return hmac.new(key, code.encode("utf-8"), hashlib.sha256).hexdigest()


def _generate_otp() -> str:
    """Genera un código numérico de 6 dígitos criptográficamente seguro."""
    return f"{secrets.randbelow(1_000_000):06d}"


async def _create_mfa_challenge(db, user: dict) -> dict:
    """Crea un challenge MFA, lo guarda en MongoDB y envía el OTP por email."""
    challenge_id = str(uuid.uuid4())
    code = _generate_otp()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=MFA_CODE_TTL_MIN)
    await db.auth_mfa_challenges.insert_one({
        "_id": challenge_id,
        "user_id": user["_id"],
        "email": user["email"],
        "code_hash": _hash_otp(code),
        "attempts": 0,
        "created_at": now.isoformat(),
        "expires_at": expires_at,  # datetime para TTL index
        "last_sent_at": now.isoformat(),
    })
    await enviar_email_codigo_mfa(
        to=user["email"], nombre=user.get("nombre") or user.get("name") or "",
        codigo=code, minutos=MFA_CODE_TTL_MIN,
    )
    return {
        "challenge_id": challenge_id,
        "expires_at": expires_at.isoformat(),
        "ttl_minutes": MFA_CODE_TTL_MIN,
        "email_hint": _mask_email(user["email"]),
    }


def _mask_email(email: str) -> str:
    """Devuelve un hint de email enmascarado: jo**@gmail.com."""
    try:
        local, domain = email.split("@", 1)
        if len(local) <= 2:
            return f"{local[0]}*@{domain}"
        return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"
    except Exception:
        return "***"


@router.post("/login")
async def login(payload: LoginIn, request: Request):
    """Paso 1 del login: valida credenciales y dispara el OTP por email.

    NO emite cookies de sesión aquí — el cliente debe llamar a /auth/mfa/verify
    con el código recibido para completar el login.
    """
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

    # Generamos el challenge MFA y enviamos OTP
    challenge = await _create_mfa_challenge(db, user)
    return {"mfa_required": True, **challenge}


@router.post("/mfa/verify")
async def mfa_verify(payload: MfaVerifyIn, request: Request, response: Response):
    """Paso 2 del login: verifica el OTP y emite las cookies de sesión."""
    db = _db(request)
    rec = await db.auth_mfa_challenges.find_one({"_id": payload.challenge_id})
    if not rec:
        raise HTTPException(404, "El código ha caducado o no es válido. Inicia sesión de nuevo.")

    expires_at = rec.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            expires_at = None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not expires_at or expires_at < datetime.now(timezone.utc):
        await db.auth_mfa_challenges.delete_one({"_id": payload.challenge_id})
        raise HTTPException(410, "El código ha caducado. Inicia sesión de nuevo.")

    if rec.get("attempts", 0) >= MFA_MAX_ATTEMPTS:
        await db.auth_mfa_challenges.delete_one({"_id": payload.challenge_id})
        raise HTTPException(429, "Demasiados intentos fallidos. Inicia sesión de nuevo.")

    submitted = payload.code.strip()
    if not hmac.compare_digest(_hash_otp(submitted), rec.get("code_hash") or ""):
        await db.auth_mfa_challenges.update_one(
            {"_id": payload.challenge_id}, {"$inc": {"attempts": 1}}
        )
        remaining = MFA_MAX_ATTEMPTS - rec.get("attempts", 0) - 1
        raise HTTPException(
            401, f"Código incorrecto. Te quedan {max(0, remaining)} intentos."
        )

    # OK — borramos el challenge y emitimos cookies
    await db.auth_mfa_challenges.delete_one({"_id": payload.challenge_id})

    user = await db.users.find_one({"_id": rec["user_id"]})
    if not user or user.get("status") != "active":
        raise HTTPException(401, "Usuario no válido")

    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"last_login": datetime.now(timezone.utc).isoformat()}},
    )
    access = create_access_token(user["_id"], user["email"])
    refresh = create_refresh_token(user["_id"])
    set_auth_cookies(response, access, refresh)

    role = await db.roles.find_one({"name": user.get("role")}) if user.get("role") else None
    user_out = _to_safe(user)
    user_out["permisos"] = sorted((role or {}).get("permissions", []))
    return user_out


@router.post("/mfa/resend")
async def mfa_resend(payload: MfaResendIn, request: Request):
    """Reenvía el OTP del mismo challenge (con throttle de 60s)."""
    db = _db(request)
    rec = await db.auth_mfa_challenges.find_one({"_id": payload.challenge_id})
    if not rec:
        raise HTTPException(404, "Sesión no encontrada. Inicia sesión de nuevo.")

    last_sent = rec.get("last_sent_at")
    if isinstance(last_sent, str):
        try:
            last_sent_dt = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
        except ValueError:
            last_sent_dt = None
    else:
        last_sent_dt = last_sent
    if last_sent_dt and last_sent_dt.tzinfo is None:
        last_sent_dt = last_sent_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if last_sent_dt and (now - last_sent_dt).total_seconds() < MFA_RESEND_THROTTLE_SEC:
        wait = MFA_RESEND_THROTTLE_SEC - int((now - last_sent_dt).total_seconds())
        raise HTTPException(429, f"Espera {wait}s antes de reenviar el código.")

    # Nuevo código + reinicia intentos + extiende caducidad
    code = _generate_otp()
    expires_at = now + timedelta(minutes=MFA_CODE_TTL_MIN)
    await db.auth_mfa_challenges.update_one(
        {"_id": payload.challenge_id},
        {"$set": {
            "code_hash": _hash_otp(code),
            "attempts": 0,
            "expires_at": expires_at,
            "last_sent_at": now.isoformat(),
        }},
    )
    user = await db.users.find_one({"_id": rec["user_id"]})
    if not user:
        raise HTTPException(404, "Usuario no encontrado")
    await enviar_email_codigo_mfa(
        to=user["email"], nombre=user.get("nombre") or user.get("name") or "",
        codigo=code, minutos=MFA_CODE_TTL_MIN,
    )
    return {"ok": True, "expires_at": expires_at.isoformat(),
            "email_hint": _mask_email(user["email"])}


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
