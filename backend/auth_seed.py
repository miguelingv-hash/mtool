"""
auth_seed.py — Seed inicial de roles y primer administrador

Se ejecuta en cada arranque del backend (es idempotente):
  - Crea los 2 roles canónicos (`admin` con wildcard `*`, `usuario` con permisos
    de lectura comunes) si no existen.
  - Crea el usuario admin a partir de `ADMIN_EMAIL` si no existe:
      * status='pending', sin password_hash
      * Genera un token de activación + envía email al admin para que defina
        su contraseña. Si Resend no está configurado, el link aparece en logs.
  - Crea índices únicos (`users.email`) y TTL (`activation_tokens.expires_at`).

Llamado desde server.py al iniciar la app.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from auth import generate_setup_token, setup_token_expiry
from email_service import enviar_email_setup_password


ROLES_DEFAULT = [
    {
        "_id": "admin",
        "name": "admin",
        "description": "Administrador con acceso total",
        "permissions": ["*"],
    },
    {
        "_id": "usuario",
        "name": "usuario",
        "description": "Acceso de lectura a consultas y comparativa",
        "permissions": [
            "consultas.unitaria",
            "consultas.batch",
            "comparativa.view",
            "conciliacion.view",
            "logs.view",
            "tasas.view",
            "tasas.manage",
            "pagos_ventanilla.view",
            "pagos_ventanilla.manage",
        ],
    },
]


async def seed_auth(db: AsyncIOMotorDatabase, logger) -> None:
    # Índices
    await db.users.create_index("email", unique=True)
    await db.roles.create_index("name", unique=True)
    await db.activation_tokens.create_index("token", unique=True)
    await db.activation_tokens.create_index("user_id")
    # No usamos TTL automático sobre expires_at porque guardamos ISO string
    # (TTL exige BSON Date). Limpieza ad-hoc se hace al usar el token.

    # 1) Roles — siempre refresca permisos canónicos
    for r in ROLES_DEFAULT:
        await db.roles.update_one(
            {"_id": r["_id"]},
            {
                "$set": {
                    "name": r["name"],
                    "description": r["description"],
                    "permissions": r["permissions"],
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()},
            },
            upsert=True,
        )

    # 2) Admin
    admin_email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    admin_name = (os.environ.get("ADMIN_NAME") or "Administrador").strip()
    if not admin_email:
        logger.warning("[seed_auth] ADMIN_EMAIL no configurado; saltando seed admin")
        return

    existing = await db.users.find_one({"email": admin_email})
    if existing:
        # Si ya existe pero no está activo y no tiene token vigente, regenera uno
        if existing.get("status") == "pending":
            vigente = await db.activation_tokens.find_one({
                "user_id": existing["_id"],
                "used": False,
            })
            if not vigente:
                token = generate_setup_token()
                await db.activation_tokens.insert_one({
                    "_id": token,
                    "token": token,
                    "user_id": existing["_id"],
                    "motivo": "bootstrap",
                    "expires_at": setup_token_expiry().isoformat(),
                    "used": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                await enviar_email_setup_password(
                    to=admin_email,
                    nombre=existing.get("name") or admin_name,
                    token=token,
                    motivo="bootstrap",
                )
                logger.info("[seed_auth] Nuevo token de activación enviado al admin")
        else:
            logger.info("[seed_auth] Admin ya activo: %s", admin_email)
        return

    # No existía → lo creamos en estado pending y enviamos el email de setup
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.users.insert_one({
        "_id": user_id,
        "email": admin_email,
        "name": admin_name,
        "role": "admin",
        "status": "pending",
        "password_hash": None,
        "created_at": now,
        "invited_by": "system",
    })
    token = generate_setup_token()
    await db.activation_tokens.insert_one({
        "_id": token,
        "token": token,
        "user_id": user_id,
        "motivo": "bootstrap",
        "expires_at": setup_token_expiry().isoformat(),
        "used": False,
        "created_at": now,
    })
    res = await enviar_email_setup_password(
        to=admin_email,
        nombre=admin_name,
        token=token,
        motivo="bootstrap",
    )
    logger.info(
        "[seed_auth] Admin creado (%s). Estado email=%s. URL=/activar/%s",
        admin_email, res.get("status"), token,
    )
