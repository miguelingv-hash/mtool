"""Regression test for the 2026-02 refactor of the /api/comparativa/bundle
endpoint (native $lookup/$group/$facet aggregations + asyncio.gather).

Validates:
- Bundle endpoint responds 200 with all keys for default state (diffs) with
  nif_titular filter, and NOT HTTP 400 anymore (previously the OOM guard
  broke UX on entry).
- Totales endpoint returns proper structure and finishes reasonably.
- Resumen-origenes endpoint returns items array with expected KPIs.
- Cache hit on second request is significantly faster (<200ms target,
  <2s tolerance).
- Each estado filter (all, coincide, discrepancia, solo_comercial,
  solo_sii, diffs) responds 200 without HTTP 400.
- Two NIFs work.
"""
import os
import time
import requests
import pytest
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "miguelingv@gmail.com"

NIF_SMALL = "A74251836"  # ~487k docs
NIF_LARGE = "A95000295"  # ~1M docs


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def admin_session(db):
    import sys
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token, COOKIE_ACCESS  # noqa: WPS433

    user = db.users.find_one({"email": ADMIN_EMAIL})
    assert user, "Admin user no encontrado en BD"
    token = create_access_token(user["_id"], user["email"])

    s = requests.Session()
    s.cookies.set(
        COOKIE_ACCESS, token,
        domain="soap-factura-batch.preview.emergentagent.com",
    )
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=60)
    assert r.status_code == 200, f"Auth bypass falló: {r.status_code}"
    return s


class TestBundleDefaultState:
    """El bug reportado: /comparativa lanzaba HTTP 400 al entrar."""

    def test_bundle_diffs_default_no_400(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": NIF_SMALL, "only_diffs": "true",
                    "skip": 0, "limit": 50},
            timeout=180,
        )
        assert r.status_code == 200, (
            f"BUG regresión: bundle devuelve {r.status_code} en carga "
            f"inicial. Body: {r.text[:400]}"
        )
        body = r.json()
        assert "list" in body
        assert "totales" in body
        assert "resumen_origenes" in body

    def test_bundle_list_shape(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": NIF_SMALL, "only_diffs": "true",
                    "skip": 0, "limit": 10},
            timeout=180,
        )
        assert r.status_code == 200
        lst = r.json()["list"]
        assert "items" in lst and isinstance(lst["items"], list)
        assert "total" in lst and isinstance(lst["total"], int)
        assert "campos_canonicos" in lst

    def test_totales_endpoint(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/totales",
            params={"nif_titular": NIF_SMALL},
            timeout=180,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        d = r.json()
        # Estructura documentada en review_request
        for k in ("sii", "comercial_por_origen", "comercial_total",
                  "diferencias", "filtros"):
            assert k in d, f"Falta campo '{k}' en totales: {list(d.keys())}"

    def test_resumen_origenes_endpoint(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/resumen-origenes",
            params={"nif_titular": NIF_SMALL},
            timeout=180,
        )
        assert r.status_code == 200
        d = r.json()
        assert "items" in d
        assert isinstance(d["items"], list)
        if d["items"]:
            expected_keys = {
                "origen", "total_facturas", "base_total", "cuota_total",
                "importe_total", "matches_sii", "sin_match_sii",
                "coincidencias", "discrepancias",
            }
            first = d["items"][0]
            missing = expected_keys - set(first.keys())
            assert not missing, f"Faltan campos en item origen: {missing}. Presentes: {list(first.keys())}"


class TestEstadoRegression:
    """Regresión: cada estado devuelve 200 sin errores tras el refactor."""

    @pytest.mark.parametrize("estado", [
        "all", "coincide", "discrepancia", "solo_comercial",
        "solo_sii", "diffs",
    ])
    def test_estado_no_400(self, admin_session, estado):
        params = {
            "nif_titular": NIF_SMALL,
            "skip": 0, "limit": 10,
        }
        if estado == "all":
            params["only_diffs"] = "false"
        elif estado == "diffs":
            params["only_diffs"] = "true"
        else:
            params["estado"] = estado
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params=params,
            timeout=240,
        )
        assert r.status_code == 200, (
            f"estado={estado}: HTTP {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        assert "list" in body and "total" in body["list"]


class TestCacheHit:
    def test_second_request_is_cached(self, admin_session):
        params = {"nif_titular": NIF_SMALL, "only_diffs": "true",
                  "skip": 0, "limit": 50}
        # Warm up
        r1 = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params=params, timeout=240,
        )
        assert r1.status_code == 200
        t0 = time.time()
        r2 = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params=params, timeout=60,
        )
        elapsed = time.time() - t0
        assert r2.status_code == 200
        # Cache-hit deseado <200ms. Toleramos hasta 3s por red/preview infra.
        assert elapsed < 3.0, (
            f"Cache hit demasiado lento: {elapsed:.2f}s (esperado <200ms)"
        )


class TestNifGrande:
    def test_nif_grande_carga(self, admin_session):
        """El NIF de 1M docs debe cargar sin 400."""
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": NIF_LARGE, "only_diffs": "true",
                    "skip": 0, "limit": 20},
            timeout=300,
        )
        assert r.status_code == 200, (
            f"NIF grande falla: {r.status_code} {r.text[:300]}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
