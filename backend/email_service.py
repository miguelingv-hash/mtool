"""
email_service.py — Envío de emails transaccionales vía Resend

Si `RESEND_API_KEY` no está configurada (o falla la API), hace fallback a
log en stdout para no bloquear el flujo de auth: el admin puede copiar el
link del log y entregárselo manualmente al usuario.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import resend  # type: ignore

_logger = logging.getLogger("monitorsii.email")


def _api_key() -> Optional[str]:
    return os.environ.get("RESEND_API_KEY") or None


def _sender() -> str:
    return os.environ.get("SENDER_EMAIL") or "onboarding@resend.dev"


def _app_url() -> str:
    return (os.environ.get("APP_URL") or "").rstrip("/") or "http://localhost:3000"


# ---------------------------------------------------------------------------
# Bajo nivel
# ---------------------------------------------------------------------------
async def send_email(*, to: str, subject: str, html: str) -> dict:
    """Envía un email vía Resend. Si no hay API key configurada, hace
    fallback a log y devuelve `{status: "logged"}`. Nunca lanza para no
    romper los flujos de auth si el provider está caído.
    """
    key = _api_key()
    if not key:
        _logger.warning(
            "[EMAIL FALLBACK] Sin RESEND_API_KEY. Email NO enviado a %s.\n"
            "  Subject: %s\n"
            "  Body (HTML):\n%s",
            to, subject, html,
        )
        return {"status": "logged", "reason": "no_api_key"}

    resend.api_key = key
    params = {
        "from": _sender(),
        "to": [to],
        "subject": subject,
        "html": html,
    }
    try:
        # Resend SDK es síncrono; lo movemos a thread pool.
        res = await asyncio.to_thread(resend.Emails.send, params)
        _logger.info("Email enviado a %s (id=%s)", to, (res or {}).get("id"))
        return {"status": "sent", "id": (res or {}).get("id")}
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Fallo al enviar email a %s: %s", to, exc)
        # Fallback: logueamos el HTML para que el admin pueda recuperar el link
        _logger.warning("[EMAIL FALLBACK HTML]\n%s", html)
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Plantillas (inline CSS, tablas, UTF-8, ES)
# ---------------------------------------------------------------------------
def _layout(titulo: str, cuerpo_html: str) -> str:
    return f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><title>{titulo}</title></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background:#f4f5f7;color:#0f172a;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden;">
        <tr><td style="padding:24px 32px;border-bottom:1px solid #e2e8f0;background:#0f172a;color:#fff;">
          <div style="font-size:18px;font-weight:600;letter-spacing:.3px;">Corporate App</div>
          <div style="font-size:13px;opacity:.7;margin-top:2px;">Conciliación SII ↔ Comercial</div>
        </td></tr>
        <tr><td style="padding:28px 32px;font-size:14px;line-height:1.55;">
          {cuerpo_html}
        </td></tr>
        <tr><td style="padding:18px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;color:#64748b;font-size:12px;">
          Si no esperabas este mensaje, ignóralo. Este enlace expira a las 48 h.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""


async def enviar_email_setup_password(
    *, to: str, nombre: str, token: str, motivo: str = "alta"
) -> dict:
    """Envía el correo con el enlace para que el usuario establezca su contraseña.

    `motivo`: 'alta' (admin lo invita) | 'reset' (olvidó contraseña) | 'bootstrap'
    """
    link = f"{_app_url()}/activar/{token}"
    intro = {
        "alta": "Se ha creado una cuenta para ti en Corporate App.",
        "reset": "Has solicitado restablecer tu contraseña en Corporate App.",
        "bootstrap": "Se ha creado la cuenta de administrador inicial de Corporate App.",
    }.get(motivo, "Se ha creado una cuenta para ti en Corporate App.")

    cta = "Activar cuenta" if motivo != "reset" else "Restablecer contraseña"
    cuerpo = f"""
<p>Hola <strong>{nombre or to}</strong>,</p>
<p>{intro} Para definir tu contraseña pulsa el siguiente botón:</p>
<p style="text-align:center;margin:28px 0;">
  <a href="{link}" style="display:inline-block;padding:12px 22px;background:#0f172a;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">{cta}</a>
</p>
<p style="font-size:12px;color:#64748b;">Si el botón no funciona, copia y pega esta URL en tu navegador:<br>
<a href="{link}" style="color:#0f172a;word-break:break-all;">{link}</a></p>
"""
    return await send_email(
        to=to,
        subject="Activa tu cuenta · Corporate App",
        html=_layout("Activación de cuenta", cuerpo),
    )
