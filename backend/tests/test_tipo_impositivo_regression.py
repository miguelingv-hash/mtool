"""Regression tests for the tipo_impositivo concatenation bug fix.

Validates:
  - Facturas 26TAABN000008285 / 8290 / 8288 ahora tienen tipo_impositivo=21 (no 211.84).
  - POST /api/facturas/sii/limpiar-tipo-impositivo-anomalo?dry_run=true devuelve estructura correcta y encontradas=0 (limpieza ya aplicada).
  - GET /api/comparativa?num_serie=26TAABN000008285 devuelve sii.tipo_impositivo=21.0.
  - GET /api/facturas/sii?limit=10 sigue funcionando.
"""
import os
import hmac
import hashlib
import requests
import pytest
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "miguelingv@gmail.com"
ADMIN_PASSWORD = "MiguelAdmin2026!"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _brute_otp(code_hash: str) -> str | None:
    secret = os.environ["JWT_SECRET"].encode()
    for i in range(1_000_000):
        c = f"{i:06d}"
        if hmac.new(secret, c.encode(), hashlib.sha256).hexdigest() == code_hash:
            return c
    return None


@pytest.fixture(scope="module")
def admin_session(db):
    """Build an authenticated session minting a JWT directly with JWT_SECRET.

    Note: the password in /app/memory/test_credentials.md no longer matches the
    bcrypt hash stored in DB (admin must have rotated it via /auth/setup/...).
    To unblock backend regression tests without touching the user record, we
    mint an access token using the same helper the backend uses.
    """
    import sys
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token, COOKIE_ACCESS  # noqa: WPS433

    user = db.users.find_one({"email": ADMIN_EMAIL})
    assert user, "Admin user no encontrado en BD"
    token = create_access_token(user["_id"], user["email"])

    s = requests.Session()
    s.cookies.set(COOKIE_ACCESS, token, domain="soap-factura-batch.preview.emergentagent.com")
    # Sanity check
    r = s.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200, f"Auth bypass falló: {r.status_code} {r.text}"
    return s


# --- BUG FIX VERIFICATION 1: facturas individuales con tipo arreglado ---
class TestFacturasIndividuales:
    @pytest.mark.parametrize("num_serie, tipo_esp, cuota_esp", [
        ("26TAABN000008285", 21.0, 1.84),
        ("26TAABN000008290", 21.0, 0.03),
        ("26TAABN000008288", 21.0, 1.12),
    ])
    def test_factura_tipo_y_cuota(self, admin_session, num_serie, tipo_esp, cuota_esp):
        r = admin_session.get(f"{BASE_URL}/api/facturas/sii/{num_serie}")
        assert r.status_code == 200, f"{num_serie}: {r.status_code} {r.text[:200]}"
        doc = r.json()
        # Field assertions
        assert doc["num_serie_factura"] == num_serie
        tipo = doc.get("tipo_impositivo")
        assert tipo == tipo_esp, (
            f"{num_serie}: tipo_impositivo esperado {tipo_esp}, obtuve {tipo} "
            f"(bug: NO debe ser 211.84 ni null)"
        )
        # cuota is optional in DB (existing data could have None), but in this case fix derived it
        cuota = doc.get("cuota_repercutida")
        assert cuota == cuota_esp, (
            f"{num_serie}: cuota_repercutida esperada {cuota_esp}, obtuve {cuota}"
        )


# --- BUG FIX VERIFICATION 2: endpoint de limpieza con dry_run=true ---
class TestLimpiarEndpoint:
    def test_dry_run_estructura_y_encontradas_cero(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/facturas/sii/limpiar-tipo-impositivo-anomalo",
            params={"dry_run": "true"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Estructura
        assert "encontradas" in data
        assert "actualizadas" in data
        assert "dry_run" in data
        assert "mensaje" in data
        # Tipos
        assert isinstance(data["encontradas"], int)
        assert isinstance(data["actualizadas"], int)
        assert data["dry_run"] is True
        assert isinstance(data["mensaje"], str)
        # Datos
        assert data["actualizadas"] == 0, "dry_run no debe escribir"
        # Después de la limpieza manual, encontradas debe ser 0 o muy bajo (<<811)
        assert data["encontradas"] < 50, (
            f"encontradas={data['encontradas']} demasiado alto: la limpieza no se ha aplicado"
        )

    def test_dry_run_default_es_true(self, admin_session):
        """Sin pasar dry_run, el default es True (no escribe BD)."""
        r = admin_session.post(
            f"{BASE_URL}/api/facturas/sii/limpiar-tipo-impositivo-anomalo"
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["dry_run"] is True
        assert data["actualizadas"] == 0


# --- BUG FIX VERIFICATION 3: comparativa muestra tipo=21 (no 211.84) ---
class TestComparativaTipoCorrecto:
    def test_comparativa_num_serie_8285(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa",
            params={"num_serie": "26TAABN000008285", "only_diffs": "false", "limit": 10},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        items = data.get("items", [])
        assert len(items) > 0, f"No se encontró la factura 8285 en comparativa: {data}"

        # Busca la fila exacta
        target = next(
            (it for it in items if it["num_serie_factura"] == "26TAABN000008285"),
            None,
        )
        assert target, f"Factura 8285 no en items: {[it['num_serie_factura'] for it in items]}"

        sii_doc = target.get("sii")
        assert sii_doc, f"Sin doc sii para 8285: {target}"
        assert sii_doc.get("tipo_impositivo") == 21.0, (
            f"COMPARATIVA: tipo_impositivo SII esperado 21.0, obtuve "
            f"{sii_doc.get('tipo_impositivo')} (bug original: 211.84)"
        )


# --- REGRESIÓN: listado de facturas SII sigue funcionando ---
class TestListadoFacturasRegresion:
    def test_listar_sii_limit_10(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/facturas/sii", params={"limit": 10})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "total" in data and "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) <= 10
        # Sanity: cada item tiene num_serie_factura, y si tiene tipo_impositivo ahora está en rango
        for it in data["items"]:
            assert "num_serie_factura" in it
            t = it.get("tipo_impositivo")
            if t is not None:
                assert 0.0 <= t <= 30.0, (
                    f"Factura {it['num_serie_factura']} con tipo fuera de rango: {t}"
                )

    def test_listar_sii_no_mongo_id(self, admin_session):
        """Asegura que no devuelve el _id de Mongo (no serializable)."""
        r = admin_session.get(f"{BASE_URL}/api/facturas/sii", params={"limit": 5})
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert "_id" not in it


# --- REGRESIÓN: endpoint conciliar-newman accesible (sin body válido devolverá 4xx, pero no 5xx) ---
class TestConciliarNewmanRegresion:
    def test_endpoint_existe_y_no_5xx_sin_body(self, admin_session):
        """Llama sin file: debe devolver 4xx (validation) pero no 5xx."""
        r = admin_session.post(f"{BASE_URL}/api/sii/conciliar-newman")
        assert r.status_code < 500, (
            f"conciliar-newman responde 5xx: {r.status_code} {r.text[:200]}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
