"""Integration tests for /api/comparativa endpoint with diff-by-tramos behavior.

UPDATED for iteration_11:
  - Cambio funcional: null cuota SII == 0.0 cuota comercial → NO es discrepancia.
  - Factura 26TAABN000008293 ahora marca estado='coincide' (antes 'discrepancia').
  - diferencias.detalle_iva NO debe aparecer (todos los tramos coinciden).
  - REGRESIÓN: endpoints redondear-importes y limpiar-tipo-impositivo-anomalo siguen 200.
  - REGRESIÓN B: una factura con tramos 21+10 sin exentas se sigue comparando OK.
"""
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
    items = payload if isinstance(payload, list) else payload.get("items") or payload.get("data") or []
    assert items, f"esperaba items, obtuve: {str(payload)[:500]}"
    item = next((x for x in items if x.get("num_serie_factura") == "26TAABN000008293"), None)
    assert item is not None, f"factura 8293 no presente: {[x.get('num_serie_factura') for x in items]}"
    return item


# --- FEATURE 1 (iteration_11): null cuota SII == 0 comercial → coincide ---
class TestFeature1NullEquivCero:
    def test_estado_coincide(self, comparativa_8293):
        """La factura 8293 antes marcaba 'discrepancia' por la exenta E1 (null vs 0).
        Ahora con la nueva regla debe marcar 'coincide'."""
        estado = comparativa_8293.get("estado")
        assert estado == "coincide", f"esperaba estado='coincide', obtuve '{estado}' — item={comparativa_8293}"

    def test_detalle_iva_no_aparece(self, comparativa_8293):
        """Si todos los tramos coinciden, detalle_iva NO debe aparecer en diferencias."""
        diffs = comparativa_8293.get("diferencias") or {}
        assert "detalle_iva" not in diffs, f"detalle_iva no debería aparecer: {list(diffs.keys())}"

    def test_diferencias_sin_campos_cabecera_iva(self, comparativa_8293):
        """No debería haber base/cuota/tipo a nivel cabecera (ambos lados tienen desglose)."""
        diffs = comparativa_8293.get("diferencias") or {}
        assert "base_imponible" not in diffs
        assert "cuota_repercutida" not in diffs
        assert "tipo_impositivo" not in diffs


# --- REGRESIÓN B: factura con tramos 21+10 (sin exentas) ---
class TestRegresionFacturaMultiTramo:
    def test_factura_con_tramos_21_y_10(self, admin_session, db):
        """Busca una factura con tramos 21+10 que exista en ambas colecciones,
        valida que el endpoint responde 200 y compara correctamente."""
        # Recolectar num_series de comercial con >=2 tramos
        comercial_series = set(
            f["num_serie_factura"]
            for f in db.facturas_comercial.find(
                {"detalle_iva.1": {"$exists": True}},
                {"num_serie_factura": 1},
            ).limit(500)
        )
        cursor = db.facturas_sii.find(
            {"detalle_iva.1": {"$exists": True}},
            {"num_serie_factura": 1, "detalle_iva": 1},
        ).limit(500)
        target = None
        for f in cursor:
            if f["num_serie_factura"] not in comercial_series:
                continue
            tipos = {(d or {}).get("tipo_impositivo") for d in (f.get("detalle_iva") or [])}
            if 21.0 in tipos and 10.0 in tipos and None not in tipos:
                target = f["num_serie_factura"]
                break
        if not target:
            pytest.skip("No se encontró factura con tramos 21+10 sin exentas presente en ambas colecciones")

        r = admin_session.get(
            f"{BASE_URL}/api/comparativa",
            params={"num_serie": target, "limit": 5, "only_diffs": "false"},
            timeout=60,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        payload = r.json()
        items = payload if isinstance(payload, list) else payload.get("items") or payload.get("data") or []
        assert items, f"sin items para {target}"
        item = next((x for x in items if x.get("num_serie_factura") == target), None)
        assert item is not None, f"factura {target} no presente"
        # Si tiene detalle_iva en diff, debe estar ordenado 21 antes que 10
        diffs = item.get("diferencias") or {}
        tramos = diffs.get("detalle_iva")
        if tramos:
            tipos_orden = [(t.get("key") or {}).get("tipo") for t in tramos if "tipo" in (t.get("key") or {})]
            if 21.0 in tipos_orden and 10.0 in tipos_orden:
                idx21 = tipos_orden.index(21.0)
                idx10 = tipos_orden.index(10.0)
                assert idx21 < idx10, f"Orden incorrecto: {tipos_orden}"


# --- REGRESIÓN A: endpoints de saneamiento siguen vivos ---
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

    def test_comparativa_sin_filtros(self, admin_session):
        """Endpoint sin filtros responde 200."""
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa",
            params={"limit": 10, "only_diffs": "false"},
            timeout=60,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        payload = r.json()
        items = payload if isinstance(payload, list) else payload.get("items") or payload.get("data") or []
        assert isinstance(items, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
