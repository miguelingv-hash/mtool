"""E2E regression test for the 'excluir_comercial_tipo_iva_cero' flag fix.

Validates the BUG FIX reported on factura 26TAAYN000009029:
when the comercial doc has detalle_iva lines with tipo_impositivo NULL or 0
and the flag is active, those lines must be:
  (1) excluded from /api/comparativa/totales aggregation
  (2) excluded from the diff_facturas tramos matching
  (3) recalculate header base_imponible/cuota_repercutida of comercial doc
      returned by /api/comparativa.

Also tests config persistence via PUT/GET /api/comparativa/config.
"""
import os
import sys
import requests
import pytest
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

sys.path.insert(0, "/app/backend")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "miguelingv@gmail.com"

# Test data identifiers (TEST_ prefix for easy cleanup)
TEST_NUM_SERIE = "TESTSEXCL001"
TEST_NIF = "A95000295"
TEST_EJERCICIO = "2026"
TEST_PERIODO = "06"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def admin_session(db):
    """Mint a JWT directly (same pattern as test_tipo_impositivo_regression.py)."""
    from auth import create_access_token, COOKIE_ACCESS  # noqa: WPS433
    user = db.users.find_one({"email": ADMIN_EMAIL})
    assert user, "Admin user no encontrado en BD"
    token = create_access_token(user["_id"], user["email"])
    s = requests.Session()
    # Set cookie on the host the BASE_URL resolves to
    host = BASE_URL.replace("https://", "").replace("http://", "").split("/")[0]
    s.cookies.set(COOKIE_ACCESS, token, domain=host)
    # Sanity check
    r = s.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200, f"Auth bypass falló: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def previous_config(admin_session):
    """Snapshot current /api/comparativa/config so we can restore after tests."""
    r = admin_session.get(f"{BASE_URL}/api/comparativa/config")
    assert r.status_code == 200, f"GET config: {r.status_code} {r.text}"
    cfg = r.json()
    yield cfg
    # Teardown: restore previous
    payload = {
        "campos_comparados": cfg.get("campos_comparados", []),
        "invertir_signo_por_origen": cfg.get("invertir_signo_por_origen", {}),
        "excluir_comercial_base_cero": cfg.get("excluir_comercial_base_cero", False),
        "excluir_comercial_tipo_iva_cero": cfg.get(
            "excluir_comercial_tipo_iva_cero", True
        ),
    }
    admin_session.put(f"{BASE_URL}/api/comparativa/config", json=payload)


@pytest.fixture
def seeded_docs(db, admin_session, previous_config):
    """Insert SII + COMERCIAL synthetic docs reproducing the bug scenario.

    Cleans up before yielding and on teardown.
    """
    # Ensure flag active + SAP invert active for this test
    payload = {
        "campos_comparados": previous_config.get("campos_comparados", []),
        "invertir_signo_por_origen": {
            **(previous_config.get("invertir_signo_por_origen") or {}),
            "SAP": True,
        },
        "excluir_comercial_base_cero": previous_config.get(
            "excluir_comercial_base_cero", False
        ),
        "excluir_comercial_tipo_iva_cero": True,
    }
    r = admin_session.put(f"{BASE_URL}/api/comparativa/config", json=payload)
    assert r.status_code == 200, f"PUT config: {r.status_code} {r.text}"

    # Cleanup any prior copies
    db.facturas_sii.delete_many({"num_serie_factura": TEST_NUM_SERIE})
    db.facturas_comercial.delete_many({"num_serie_factura": TEST_NUM_SERIE})

    sii_doc = {
        "num_serie_factura": TEST_NUM_SERIE,
        "nif_titular": TEST_NIF,
        "ejercicio": TEST_EJERCICIO,
        "periodo": TEST_PERIODO,
        "base_imponible": 7.21,
        "cuota_repercutida": 1.51,
        "tipo_impositivo": 21.0,
    }
    com_doc = {
        "num_serie_factura": TEST_NUM_SERIE,
        "nif_titular": TEST_NIF,
        "ejercicio": TEST_EJERCICIO,
        "periodo": TEST_PERIODO,
        "origen_comercial": "SAP",
        "base_imponible": -7.18,
        "cuota_repercutida": -1.51,
        "tipo_impositivo": 21.0,
        "detalle_iva": [
            {
                "tipo_impositivo": 21.0,
                "base_imponible": -7.21,
                "cuota_repercutida": -1.51,
                "origen": "SAP",
            },
            {
                "tipo_impositivo": None,
                "base_imponible": 0.03,
                "cuota_repercutida": 0.0,
                "origen": "SAP",
            },
        ],
    }
    db.facturas_sii.insert_one(sii_doc)
    db.facturas_comercial.insert_one(com_doc)

    yield {"sii": sii_doc, "com": com_doc}

    # Teardown
    db.facturas_sii.delete_many({"num_serie_factura": TEST_NUM_SERIE})
    db.facturas_comercial.delete_many({"num_serie_factura": TEST_NUM_SERIE})


# --- Test 1: GET /api/comparativa returns coincide with recalculated header ---
class TestComparativaExcluirTipoIvaCero:
    def test_comparativa_devuelve_coincide_y_filtra_linea(
        self, admin_session, seeded_docs
    ):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa",
            params={"num_serie": TEST_NUM_SERIE, "only_diffs": "false"},
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        items = body.get("items") or []
        row = next(
            (x for x in items if x.get("num_serie_factura") == TEST_NUM_SERIE),
            None,
        )
        assert row is not None, (
            f"factura sintética no devuelta por /api/comparativa: {body}"
        )

        # (1) estado = coincide
        assert row["estado"] == "coincide", (
            f"esperaba 'coincide', obtuve '{row['estado']}', "
            f"diferencias={row.get('diferencias')}"
        )
        # (2) diferencias = {}
        assert row.get("diferencias") == {}, (
            f"diferencias debería ser vacío: {row.get('diferencias')}"
        )
        # (3) comercial detalle_iva: solo 1 línea (la del tipo=21)
        com = row.get("comercial") or {}
        det = com.get("detalle_iva") or []
        assert len(det) == 1, f"esperaba 1 línea filtrada, obtuve {len(det)}: {det}"
        assert det[0].get("tipo_impositivo") == 21.0
        # (4) cabecera recalculada
        assert com.get("base_imponible") == -7.21, (
            f"base recalculada esperada -7.21, obtenida {com.get('base_imponible')}"
        )
        assert com.get("cuota_repercutida") == -1.51, (
            f"cuota recalculada esperada -1.51, obtenida "
            f"{com.get('cuota_repercutida')}"
        )

    # --- Test 2: /api/comparativa/totales excludes the null-tipo line ---
    def test_totales_excluye_linea_tipo_iva_cero(self, admin_session, seeded_docs):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/totales",
            params={
                "num_serie": TEST_NUM_SERIE,
                "ejercicio": TEST_EJERCICIO,
                "periodo": TEST_PERIODO,
            },
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()

        # SII total = 7.21 base, 1.51 cuota
        sii = body["sii"]
        assert abs(float(sii["base"]) - 7.21) < 0.005, (
            f"SII base esperada 7.21, obtuve {sii}"
        )
        assert abs(float(sii["cuota"]) - 1.51) < 0.005

        # comercial_por_origen.SAP: con SAP invert=True y línea null excluida,
        # la base resultante debe ser +7.21 (raw -7.21 → invertida) NO +7.18.
        sap = body["comercial_por_origen"].get("SAP")
        assert sap is not None, (
            f"esperaba 'SAP' en comercial_por_origen, obtuve {body['comercial_por_origen']}"
        )
        assert sap.get("invertido") is True, (
            f"esperaba SAP invertido=True, obtuve {sap}"
        )
        assert abs(float(sap["base"]) - 7.21) < 0.005, (
            f"SAP base esperada 7.21 (línea null excluida + invertida), obtuve "
            f"{sap['base']}. Si fuera 7.18, el flag NO está filtrando en /totales."
        )
        assert abs(float(sap["cuota"]) - 1.51) < 0.005

        # comercial_total = Σ orígenes
        com_tot = body["comercial_total"]
        assert abs(float(com_tot["base"]) - 7.21) < 0.005

        # diferencias.base = 7.21 - 7.21 = 0
        dif = body["diferencias"]
        assert abs(float(dif["base"])) < 0.005, (
            f"esperaba diferencia 0, obtuve {dif['base']} → flag no aplicado"
        )


# --- Test 3: Config persistence (PUT/GET round-trip) ---
class TestConfigPersistence:
    def test_config_toggle_persistence(self, admin_session, previous_config):
        # Toggle off
        payload_off = {
            "campos_comparados": previous_config.get("campos_comparados", []),
            "invertir_signo_por_origen": previous_config.get(
                "invertir_signo_por_origen", {}
            ),
            "excluir_comercial_base_cero": previous_config.get(
                "excluir_comercial_base_cero", False
            ),
            "excluir_comercial_tipo_iva_cero": False,
        }
        r = admin_session.put(
            f"{BASE_URL}/api/comparativa/config", json=payload_off
        )
        assert r.status_code == 200
        assert r.json().get("excluir_comercial_tipo_iva_cero") is False

        # GET it back
        r2 = admin_session.get(f"{BASE_URL}/api/comparativa/config")
        assert r2.status_code == 200
        assert r2.json().get("excluir_comercial_tipo_iva_cero") is False

        # Toggle on
        payload_on = {**payload_off, "excluir_comercial_tipo_iva_cero": True}
        r3 = admin_session.put(
            f"{BASE_URL}/api/comparativa/config", json=payload_on
        )
        assert r3.status_code == 200
        assert r3.json().get("excluir_comercial_tipo_iva_cero") is True

        r4 = admin_session.get(f"{BASE_URL}/api/comparativa/config")
        assert r4.status_code == 200
        assert r4.json().get("excluir_comercial_tipo_iva_cero") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
