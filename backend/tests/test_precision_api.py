"""Integration tests for the precision-float-rounding bug fix.

Validates:
  - GET /api/facturas/sii/26TAABN000008293 returns base_imponible=3.86 (not 3.8600000000000003).
  - Other related invoices (8285, 8288, 8290) also have rounded base_imponible / cuota_repercutida / importe_total.
  - POST /api/facturas/sii/redondear-importes:
      * Without auth -> 401.
      * With auth + dry_run=true -> {encontradas:int, actualizadas:0, dry_run:true, mensaje:str}.
      * Should be idempotent (encontradas=0 after a previous real run).
  - REGRESSION A: POST /api/facturas/sii/limpiar-tipo-impositivo-anomalo?dry_run=true responds {encontradas:0}.
  - REGRESSION B: GET /api/comparativa returns base_imponible/cuota_repercutida as floats with <=2 decimals.
"""
import os
import requests
import pytest
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "miguelingv@gmail.com"

TARGET_NUMSERIE = "26TAABN000008293"
RELATED_NUMSERIES = ["26TAABN000008285", "26TAABN000008288", "26TAABN000008290"]


def _max_two_decimals(v) -> bool:
    """True if v is None or round(v,2) == v exactly."""
    if v is None:
        return True
    if not isinstance(v, (int, float)):
        return False
    return round(float(v), 2) == v


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def admin_session(db):
    """Build authenticated session via direct JWT mint."""
    import sys
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token, COOKIE_ACCESS  # noqa: WPS433

    user = db.users.find_one({"email": ADMIN_EMAIL})
    assert user, "Admin user no encontrado en BD"
    token = create_access_token(user["_id"], user["email"])

    s = requests.Session()
    s.cookies.set(COOKIE_ACCESS, token, domain="soap-factura-batch.preview.emergentagent.com")
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=15)
    assert r.status_code == 200, f"Auth bypass failed: {r.status_code} {r.text}"
    return s


# --- BUG FIX 1: GET /facturas/sii/{numserie} importes con max 2 decimales ---
class TestImportesRedondeadosEnGetFactura:
    def test_factura_objetivo_base_imponible_3_86(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/facturas/sii/{TARGET_NUMSERIE}", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        bi = data.get("base_imponible")
        assert bi == 3.86, f"Esperaba base_imponible=3.86, obtuve {bi!r}"
        # Comprobaciones generales de redondeo de los demas campos
        for campo in ("base_imponible", "cuota_repercutida", "importe_total"):
            v = data.get(campo)
            assert _max_two_decimals(v), f"{campo}={v!r} tiene mas de 2 decimales"

    @pytest.mark.parametrize("num_serie", RELATED_NUMSERIES)
    def test_facturas_relacionadas_max_2_decimales(self, admin_session, num_serie):
        r = admin_session.get(f"{BASE_URL}/api/facturas/sii/{num_serie}", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        for campo in ("base_imponible", "cuota_repercutida", "importe_total"):
            v = data.get(campo)
            assert _max_two_decimals(v), (
                f"{num_serie}: {campo}={v!r} tiene mas de 2 decimales"
            )


# --- BUG FIX 2: POST /facturas/sii/redondear-importes ---
class TestRedondearImportesEndpoint:
    def test_sin_auth_401(self):
        # Una sesion vacia (sin cookies) debe ser rechazada
        r = requests.post(
            f"{BASE_URL}/api/facturas/sii/redondear-importes",
            params={"dry_run": "true"},
            timeout=15,
        )
        assert r.status_code in (401, 403), (
            f"Sin auth deberia ser 401/403, obtuve {r.status_code}: {r.text[:200]}"
        )

    def test_dry_run_estructura(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/facturas/sii/redondear-importes",
            params={"dry_run": "true"},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Estructura del response
        assert set(data.keys()) >= {"encontradas", "actualizadas", "dry_run", "mensaje"}
        assert isinstance(data["encontradas"], int)
        assert data["actualizadas"] == 0  # dry_run nunca actualiza
        assert data["dry_run"] is True
        assert isinstance(data["mensaje"], str) and data["mensaje"]

    def test_dry_run_idempotente_es_cero(self, admin_session):
        """Tras el run real ya ejecutado en BD, dry_run debe encontrar 0."""
        r = admin_session.post(
            f"{BASE_URL}/api/facturas/sii/redondear-importes",
            params={"dry_run": "true"},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["encontradas"] == 0, (
            f"BD deberia estar limpia tras dry_run=false previo. encontradas={data['encontradas']}"
        )


# --- REGRESION A: limpiar-tipo-impositivo-anomalo sigue OK ---
class TestRegresionLimpiarTipoImpositivo:
    def test_dry_run_devuelve_cero(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/facturas/sii/limpiar-tipo-impositivo-anomalo",
            params={"dry_run": "true"},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "encontradas" in data
        assert data["encontradas"] == 0, (
            f"Limpieza previa ya aplicada. encontradas={data['encontradas']}"
        )


# --- REGRESION B: GET /comparativa devuelve floats con max 2 decimales ---
class TestRegresionComparativa:
    def test_comparativa_sin_filtros_status_200(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa",
            params={"limit": 20},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:500]
        data = r.json()
        # Estructura tipica: lista o dict con items
        items = data if isinstance(data, list) else data.get("items") or data.get("results") or []
        assert isinstance(items, list), f"Estructura inesperada: keys={list(data.keys()) if isinstance(data, dict) else type(data)}"
        # Como min queremos al menos 5 ejemplos con sii
        sii_examples = []
        for it in items:
            sii = it.get("sii") if isinstance(it, dict) else None
            if isinstance(sii, dict) and sii:
                sii_examples.append(sii)
            if len(sii_examples) >= 10:
                break
        assert len(sii_examples) >= 5, f"Solo {len(sii_examples)} ejemplos con doc sii; total items={len(items)}"
        bad = []
        for sii in sii_examples:
            for campo in ("base_imponible", "cuota_repercutida", "importe_total"):
                v = sii.get(campo)
                if not _max_two_decimals(v):
                    bad.append((sii.get("num_serie_factura") or sii.get("numero_factura"), campo, v))
        assert not bad, f"Floats con >2 decimales en comparativa: {bad[:5]}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
