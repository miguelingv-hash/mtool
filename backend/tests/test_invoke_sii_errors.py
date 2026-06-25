"""Test de la clasificación de errores SOAP en _invoke_sii."""
import sys
import os
sys.path.insert(0, "/app/backend")

import asyncio

import pytest
import requests.exceptions as rex
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    """Asegura que server.py importa sin errores (necesita Mongo y JWT)."""
    monkeypatch.setenv("MONGO_URL", os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    monkeypatch.setenv("DB_NAME", os.environ.get("DB_NAME", "test_database"))
    monkeypatch.setenv(
        "JWT_SECRET", os.environ.get("JWT_SECRET", "test_jwt_secret_32chars_long_xxxxxxxxxxxx"),
    )


def _make_entrada():
    """Crea un ConsultaInput válido."""
    from server import ConsultaInput

    return ConsultaInput(
        nif_titular="A95000295",
        nombre_titular="Test",
        ejercicio="2026",
        periodo="06",
        nif_emisor="A95000295",
        num_serie_factura="X-1",
        fecha_expedicion="22-06-2026",
        entorno="preproduccion",
    )


@pytest.mark.parametrize(
    "exc, expected_status, expected_substr",
    [
        (rex.ReadTimeout("read timed out"), 504, "Timeout"),
        (rex.ConnectTimeout("connect timed out"), 504, "Timeout"),
        (rex.ConnectionError("Could not resolve sede.aeat.es"), 502, "red o DNS"),
        (rex.SSLError("certificate verify failed"), 502, "TLS/certificado"),
        (RuntimeError("error genérico inesperado"), 502, "Error en servicio SII"),
    ],
)
def test_clasificacion_errores_invoke_sii(monkeypatch, exc, expected_status, expected_substr):
    """_invoke_sii debe traducir cada tipo de excepción al HTTP correcto."""
    import server

    # Stub build_client (no toca AEAT)
    monkeypatch.setattr(server, "build_client", lambda **kwargs: object())

    # _execute_and_log lanza la excepción objetivo
    async def _fake_execute_and_log(client_impl, entrada):
        raise exc

    monkeypatch.setattr(server, "_execute_and_log", _fake_execute_and_log)

    entrada = _make_entrada()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server._invoke_sii(entrada, cert_bytes=None, cert_password=None))

    assert exc_info.value.status_code == expected_status
    assert expected_substr in str(exc_info.value.detail), (
        f"Expected substring {expected_substr!r} in detail {exc_info.value.detail!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
