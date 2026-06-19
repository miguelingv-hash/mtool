"""
auth.py — Autenticación y autorización
=======================================

Helpers de password (bcrypt), JWT (access 15min, refresh 7d), cookies HTTP-only
y dependencias FastAPI para proteger endpoints por permiso (RBAC dinámico).

Los roles y permisos viven en MongoDB (`roles`, `users`); el set de permisos
del usuario se resuelve en cada `get_current_user` (sin cache para que el
sistema reaccione en cuanto el admin cambia un rol).
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase

# -----------------------------------------------------------------------------
# Constantes
# -----------------------------------------------------------------------------
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MIN = 60 * 4         # 4h — equilibrio entre comodidad y seguridad
REFRESH_TOKEN_DAYS = 7
COOKIE_ACCESS = "monitorsii_access"
COOKIE_REFRESH = "monitorsii_refresh"
SETUP_TOKEN_HOURS = 48            # validez del link de "definir contraseña"
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


def _jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET no configurado en backend/.env")
    return secret


# -----------------------------------------------------------------------------
# Password hashing
# -----------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# -----------------------------------------------------------------------------
# JWT
# -----------------------------------------------------------------------------
def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MIN),
        "type": "access",
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_DAYS),
        "type": "refresh",
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sesión expirada")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Tipo de token incorrecto")
    return payload


# -----------------------------------------------------------------------------
# Cookies
# -----------------------------------------------------------------------------
def set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    common = {"httponly": True, "samesite": "lax", "secure": True, "path": "/"}
    response.set_cookie(COOKIE_ACCESS, access, max_age=ACCESS_TOKEN_MIN * 60, **common)
    response.set_cookie(COOKIE_REFRESH, refresh, max_age=REFRESH_TOKEN_DAYS * 86400, **common)


def clear_auth_cookies(response: Response) -> None:
    # Importante: para que el navegador/curl invalide la cookie hay que llamar
    # a set_cookie con los MISMOS atributos (samesite, secure, path, httponly)
    # y valor vacío + max_age=0. `delete_cookie` por defecto no los manda y
    # algunos clientes mantienen la cookie viva.
    common = {"httponly": True, "samesite": "lax", "secure": True, "path": "/"}
    response.set_cookie(COOKIE_ACCESS, "", max_age=0, **common)
    response.set_cookie(COOKIE_REFRESH, "", max_age=0, **common)


# -----------------------------------------------------------------------------
# Setup / reset tokens (un solo uso, expiración configurable)
# -----------------------------------------------------------------------------
def generate_setup_token() -> str:
    """Token URL-safe de 32 bytes para enviar por email (no es JWT)."""
    return secrets.token_urlsafe(32)


def setup_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=SETUP_TOKEN_HOURS)


# -----------------------------------------------------------------------------
# Dependencia FastAPI: usuario actual (lee cookie → token → carga BD + roles)
# -----------------------------------------------------------------------------
async def _resolve_user(db: AsyncIOMotorDatabase, user_id: str) -> Optional[dict]:
    user = await db.users.find_one({"_id": user_id})
    if not user:
        return None
    user.pop("password_hash", None)
    # Resuelve permisos efectivos a partir del rol
    role_name = user.get("role")
    permisos: set[str] = set()
    if role_name:
        role = await db.roles.find_one({"name": role_name})
        if role:
            permisos.update(role.get("permissions", []))
    user["permisos"] = sorted(permisos)
    return user


def _extract_token(request: Request) -> Optional[str]:
    token = request.cookies.get(COOKIE_ACCESS)
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


async def get_current_user(request: Request) -> dict:
    """Dependencia para endpoints autenticados. Lanza 401 si no hay sesión."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    payload = decode_token(token, "access")
    db: AsyncIOMotorDatabase = request.app.state.mongo_db
    user = await _resolve_user(db, payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail=f"Cuenta {user.get('status', 'no activa')}")
    return user


def require_permission(*permisos_requeridos: str):
    """Factoría de dependencia que valida que el usuario tiene TODOS los
    permisos pedidos. Usar: `Depends(require_permission('users.manage'))`.

    El usuario 'admin' tiene siempre el wildcard '*' y pasa cualquier check.
    """
    async def _checker(user: dict = Depends(get_current_user)) -> dict:
        permisos = set(user.get("permisos") or [])
        if "*" in permisos:
            return user
        falta = [p for p in permisos_requeridos if p not in permisos]
        if falta:
            raise HTTPException(
                status_code=403,
                detail=f"Faltan permisos: {', '.join(falta)}",
            )
        return user
    return _checker


# -----------------------------------------------------------------------------
# Brute force: lockout simple por (ip + email)
# -----------------------------------------------------------------------------
async def is_locked_out(db: AsyncIOMotorDatabase, identifier: str) -> bool:
    doc = await db.login_attempts.find_one({"_id": identifier})
    if not doc:
        return False
    if doc.get("count", 0) < LOCKOUT_THRESHOLD:
        return False
    last = doc.get("last")
    if not last:
        return False
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(last)
        except ValueError:
            return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    unlock_at = last + timedelta(minutes=LOCKOUT_MINUTES)
    return datetime.now(timezone.utc) < unlock_at


async def register_failed_attempt(db: AsyncIOMotorDatabase, identifier: str) -> None:
    await db.login_attempts.update_one(
        {"_id": identifier},
        {"$inc": {"count": 1}, "$set": {"last": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


async def reset_attempts(db: AsyncIOMotorDatabase, identifier: str) -> None:
    await db.login_attempts.delete_one({"_id": identifier})
