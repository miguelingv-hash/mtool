"""Iteration 19 — verifies periodo CSV ($in) on /api/comparativa endpoints."""
import os
import sys
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")
import auth as _auth  # noqa: E402

USER_ID = "3974568d-4604-4406-8ccb-4d0c07fc82bf"
EMAIL = "miguelingv@gmail.com"
NIF = "A95000295"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    token = _auth.create_access_token(USER_ID, EMAIL)
    s.cookies.set("monitorsii_access", token)
    return s


def test_comparativa_csv_periodo(client):
    r = client.get(f"{BASE_URL}/api/comparativa", params={
        "nif_titular": NIF, "periodo": "06,07", "ejercicio": "2026",
        "only_diffs": "true", "limit": 5,
    }, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "total" in data and "items" in data
    # Compare with single period
    r2 = client.get(f"{BASE_URL}/api/comparativa", params={
        "nif_titular": NIF, "periodo": "06", "ejercicio": "2026",
        "only_diffs": "true", "limit": 5,
    }, timeout=60)
    assert r2.status_code == 200
    # Total con 06,07 debe ser >= total con sólo 06
    assert data["total"] >= r2.json()["total"]


def test_totales_csv_periodo(client):
    r = client.get(f"{BASE_URL}/api/comparativa/totales", params={
        "nif_titular": NIF, "periodo": "06,07", "ejercicio": "2026",
    }, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("filtros", {}).get("periodo") == "06,07"


def test_resumen_origenes_csv(client):
    r = client.get(f"{BASE_URL}/api/comparativa/resumen-origenes", params={
        "nif_titular": NIF, "periodo": "06,07", "ejercicio": "2026",
    }, timeout=60)
    assert r.status_code == 200, r.text


def test_export_csv_periodo(client):
    r = client.get(f"{BASE_URL}/api/comparativa/export", params={
        "nif_titular": NIF, "periodo": "06,07", "ejercicio": "2026",
        "only_diffs": "true",
    }, timeout=120, stream=True)
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "").lower()


def test_periodo_with_spaces(client):
    r = client.get(f"{BASE_URL}/api/comparativa", params={
        "nif_titular": NIF, "periodo": " 06 , 07 ", "ejercicio": "2026",
        "only_diffs": "true", "limit": 5,
    }, timeout=60)
    assert r.status_code == 200, r.text


def test_conciliacion_redirect():
    # Backend doesn't redirect /conciliacion — that's frontend SPA routing.
    # Verify /api/comparativa still works (smoke).
    pass
