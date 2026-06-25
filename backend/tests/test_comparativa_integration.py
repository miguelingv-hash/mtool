"""Integration tests for /api/comparativa endpoint with diff-by-tramos behavior.

FEATURES tested:
  - F1: diferencias.detalle_iva con 2 tramos (21 coincide, E1 diff por null vs 0)
  - F2: con desglose en ambos lados, base/cuota/tipo NO aparecen en cabecera
  - F3: matching relajado — tramo SII exento E1 empareja con tramo comercial sin causa
  - REGRESIÓN: endpoints redondear-importes y limpiar-tipo-impositivo-anomalo siguen 200
"""
import json
import os
import sys

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "miguelingv@gmail.com"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def admin_session(db):
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


@pytest.fixture(scope="module")
def comparativa_8293(admin_session):
    r = admin_session.get(
        f"{BASE_URL}/api/comparativa",
        params={"num_serie": "26TAABN000008293", "limit": 5, "only_diffs": "false"},
        timeout=60,
    )
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:500]}"
    payload = r.json()
    # Endpoint puede devolver lista o {items:[...]}
    items = payload if isinstance(payload, list) else payload.get("items") or payload.get("data") or []
    assert items, f"esperaba items, obtuve: {str(payload)[:500]}"
    item = next((x for x in items if x.get("num_serie_factura") == "26TAABN000008293"), None)
    assert item is not None, f"factura 8293 no presente: {[x.get('num_serie_factura') for x in items]}"
    return item


# --- FEATURE 1: detalle_iva como array de 2 tramos ---
class TestFeature1DiffPorTramos:
    def test_diferencias_contiene_detalle_iva(self, comparativa_8293):
        diffs = comparativa_8293.get("diferencias") or {}
        assert "detalle_iva" in diffs, f"diferencias sin detalle_iva: {list(diffs.keys())}"

    def test_dos_tramos(self, comparativa_8293):
        tramos = comparativa_8293["diferencias"]["detalle_iva"]
        assert isinstance(tramos, list)
        assert len(tramos) == 2, f"esperaba 2 tramos, obtuve {len(tramos)}: {json.dumps(tramos, default=str)[:500]}"

    def test_tramo_21_coincide(self, comparativa_8293):
        tramos = comparativa_8293["diferencias"]["detalle_iva"]
        t21 = next((t for t in tramos if (t.get("key") or {}).get("tipo") == 21.0), None)
        assert t21 is not None, f"no encuentro tramo 21: {tramos}"
        assert t21["diff"] is False, f"tramo 21 debería coincidir: {t21}"
        assert t21["sii"]["base_imponible"] == 3.87
        assert t21["sii"]["cuota_repercutida"] == 0.81
        assert t21["comercial"]["base_imponible"] == 3.87
        assert t21["comercial"]["cuota_repercutida"] == 0.81

    def test_tramo_exento_diff_por_null_vs_0(self, comparativa_8293):
        tramos = comparativa_8293["diferencias"]["detalle_iva"]
        texe = next((t for t in tramos if (t.get("key") or {}).get("causa_exencion") == "E1"), None)
        assert texe is not None, f"no encuentro tramo E1: {tramos}"
        assert texe["diff"] is True, f"tramo E1 debería tener diff=True: {texe}"
        assert texe["sii"]["cuota_repercutida"] is None
        # 0.0 o -0.0 ambos aceptables (mantiene la discrepancia null vs valor)
        assert texe["comercial"]["cuota_repercutida"] in (0.0, -0.0)


# --- FEATURE 2: cabecera (base/cuota/tipo) NO se compara cuando hay desglose ---
class TestFeature2CabeceraNoSeCompara:
    def test_sin_base_imponible_cabecera(self, comparativa_8293):
        diffs = comparativa_8293.get("diferencias") or {}
        assert "base_imponible" not in diffs, f"keys: {list(diffs.keys())}"

    def test_sin_cuota_cabecera(self, comparativa_8293):
        diffs = comparativa_8293.get("diferencias") or {}
        assert "cuota_repercutida" not in diffs, f"keys: {list(diffs.keys())}"

    def test_sin_tipo_cabecera(self, comparativa_8293):
        diffs = comparativa_8293.get("diferencias") or {}
        assert "tipo_impositivo" not in diffs, f"keys: {list(diffs.keys())}"


# --- FEATURE 3: matching relajado (SII exento con causa ↔ comercial sin causa) ---
class TestFeature3MatchingRelajado:
    def test_tramo_exento_emparejado(self, comparativa_8293):
        tramos = comparativa_8293["diferencias"]["detalle_iva"]
        texe = next((t for t in tramos if (t.get("key") or {}).get("causa_exencion") == "E1"), None)
        assert texe is not None
        # Ambos lados presentes ⇒ matching relajado funcionó (el comercial no tenía causa_exencion)
        assert texe["sii"] is not None, f"SII null → no se emparejó: {texe}"
        assert texe["comercial"] is not None, f"comercial null → no se emparejó: {texe}"


# --- REGRESIÓN: endpoints de saneamiento vivos ---
class TestRegresionEndpoints:
    def test_redondear_importes_dry_run(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/facturas/sii/redondear-importes",
            params={"dry_run": "true"},
            timeout=120,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert data.get("encontradas") == 0, f"BD debería estar saneada: {data}"
        assert data.get("dry_run") is True

    def test_limpiar_tipo_impositivo_anomalo_dry_run(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/facturas/sii/limpiar-tipo-impositivo-anomalo",
            params={"dry_run": "true"},
            timeout=120,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert data.get("encontradas") == 0, f"BD debería estar saneada: {data}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
