"""E2E tests for the new SAP/SIGLO Soc.→NIF feature, sociedades catalog,
selective wipe (scope=todo|sii|comercial), and backfill endpoint.

Coverage:
  - GET /api/admin/sociedades returns merged seed + persisted
  - PUT /api/admin/sociedades override + cleanup
  - POST /api/admin/comercial/asignar-nif-titular-por-soc dry_run
  - POST /api/admin/sii/vaciar-modulo with scope=todo|sii|comercial + invalid
  - GET /api/comparativa/nifs-titulares enriched with `sociedades`
  - _parsear_report_tabular unit test for Soc. mapping
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
EXPECTED_NIF = "A95000295"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def admin_session(db):
    from auth import create_access_token, COOKIE_ACCESS  # noqa

    user = db.users.find_one({"email": ADMIN_EMAIL})
    assert user, "Admin user not found"
    token = create_access_token(user["_id"], user["email"])
    s = requests.Session()
    s.cookies.set(
        COOKIE_ACCESS, token,
        domain="soap-factura-batch.preview.emergentagent.com",
    )
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    assert r.status_code == 200, r.text
    return s


# --- 1) Catálogo Sociedades --------------------------------------------------
class TestSociedadesCatalogo:
    def test_get_returns_seed(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/sociedades", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "sociedades" in data
        assert "seed" in data
        assert "persisted" in data
        socs = data["sociedades"]
        assert "4432" in socs
        assert socs["4432"]["nif_titular"] == "A95000295"
        assert "TotalEnergies" in socs["4432"]["nombre_titular"]
        assert "2239" in socs
        assert socs["2239"]["nif_titular"] == "A74251836"
        assert socs["2239"]["nombre_titular"] == "BASER"
        # Seed should be the canonical default
        assert data["seed"]["4432"]["nif_titular"] == "A95000295"

    def test_put_override_and_cleanup(self, admin_session):
        # 1) Override with TEST entry
        payload = {
            "entries": {
                "5555": {
                    "nif_titular": "B12345678",
                    "nombre_titular": "TEST_NEW",
                }
            }
        }
        r = admin_session.put(
            f"{BASE_URL}/api/admin/sociedades",
            json=payload,
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["count"] == 1
        assert data["entries"]["5555"]["nif_titular"] == "B12345678"

        # 2) GET should include override AND seeds
        r2 = admin_session.get(f"{BASE_URL}/api/admin/sociedades", timeout=30)
        assert r2.status_code == 200
        merged = r2.json()["sociedades"]
        assert "5555" in merged
        assert merged["5555"]["nif_titular"] == "B12345678"
        # Seeds still present
        assert "4432" in merged and "2239" in merged

        # 3) Cleanup → restore empty overrides (seeds remain)
        r3 = admin_session.put(
            f"{BASE_URL}/api/admin/sociedades",
            json={"entries": {}},
            timeout=30,
        )
        assert r3.status_code == 200
        cleaned = r3.json()
        assert cleaned["count"] == 0

        # Verify post-cleanup
        r4 = admin_session.get(f"{BASE_URL}/api/admin/sociedades", timeout=30)
        merged2 = r4.json()["sociedades"]
        assert "5555" not in merged2
        assert "4432" in merged2


# --- 2) Backfill comercial dry_run ------------------------------------------
class TestBackfillDryRun:
    def test_dry_run_no_alterations(self, admin_session, db):
        # Snapshot count of docs without nif_titular before
        before = db.facturas_comercial.count_documents({
            "$or": [
                {"nif_titular": None},
                {"nif_titular": ""},
                {"nif_titular": {"$exists": False}},
            ]
        })

        r = admin_session.post(
            f"{BASE_URL}/api/admin/comercial/asignar-nif-titular-por-soc",
            json={"dry_run": True},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "resumen" in data
        resumen = data["resumen"]
        for k in ("por_soc", "fallback", "sin_asignar"):
            assert k in resumen, f"missing {k} in resumen"
            assert isinstance(resumen[k], int)
        assert "detalle_por_soc" in data
        assert isinstance(data["detalle_por_soc"], dict)
        assert data.get("fallback_aplicado") is False
        assert data.get("dry_run") is True

        # Verify no doc was mutated
        after = db.facturas_comercial.count_documents({
            "$or": [
                {"nif_titular": None},
                {"nif_titular": ""},
                {"nif_titular": {"$exists": False}},
            ]
        })
        assert after == before, f"dry_run modified data: {before} → {after}"


# --- 3) Parser unit test ----------------------------------------------------
class TestParserSocColumn:
    def test_parser_maps_soc_to_nif(self):
        from router_facturas import _parsear_report_tabular

        catalogo = {
            "4432": {"nif_titular": "A95000295",
                     "nombre_titular": "TotalEnergies Clientes S.A.U."},
            "2239": {"nif_titular": "A74251836", "nombre_titular": "BASER"},
        }
        # Mini SAP report. Header must contain the signatures:
        # "Soc.", "Doc.causante", "Nº doc.oficial", "Tp.impos.",
        # "BaseImpon", "Impto.ML"; plus 2 occurrences of Fe.doc.or.
        text = (
            "Some preamble line\n"
            "|Soc.|Doc.causante|Nº doc.oficial|Fe.doc.or.|Fe.doc.or.|Tp.impos.|BaseImpon|Impto.ML|\n"
            "|----|------------|--------------|----------|----------|--------|--------|--------|\n"
            "|4432|DOC1        |INV001        |15.01.2025|15.01.2025|   21,00|  100,00|   21,00|\n"
            "|2239|DOC2        |INV002        |16.01.2025|16.01.2025|   10,00|  200,00|   20,00|\n"
            "|9999|DOC3        |INV003        |17.01.2025|17.01.2025|   21,00|  300,00|   63,00|\n"
        )
        registros, errores = _parsear_report_tabular(
            text, "SAP", catalogo_sociedades=catalogo,
        )
        assert len(registros) == 3, f"got {len(registros)}: {registros}"
        by_num = {r["num_serie_factura"]: r for r in registros}
        # 4432 → TotalEnergies
        r1 = by_num["INV001"]
        assert r1["soc_origen"] == "4432"
        assert r1["nif_titular"] == "A95000295"
        assert r1["nombre_titular"] == "TotalEnergies Clientes S.A.U."
        # 2239 → BASER
        r2 = by_num["INV002"]
        assert r2["soc_origen"] == "2239"
        assert r2["nif_titular"] == "A74251836"
        # 9999 → unmapped
        r3 = by_num["INV003"]
        assert r3["soc_origen"] == "9999"
        assert r3["nif_titular"] is None
        assert r3["nombre_titular"] is None

        # An error must report 9999 as unmapped
        unmapped_errors = [
            e for e in errores
            if "no encontradas en el catálogo" in str(e.get("motivo", ""))
        ]
        assert unmapped_errors, f"Expected unmapped-soc error, got {errores}"
        assert "9999" in str(unmapped_errors[0]["motivo"])


# --- 4) Comparativa nifs-titulares enriched ---------------------------------
class TestComparativaSociedadesEnriched:
    def test_returns_sociedades_with_nombre(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/nifs-titulares", timeout=60,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "sociedades" in data
        socs = data["sociedades"]
        assert isinstance(socs, list)
        info = next((s for s in socs if s.get("nif_titular") == EXPECTED_NIF), None)
        assert info is not None, f"Expected {EXPECTED_NIF} in {socs}"
        assert "TotalEnergies" in (info.get("nombre_titular") or "")

    def test_comercial_sin_nif_after_backfill(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/nifs-titulares", timeout=60,
        )
        assert r.status_code == 200
        data = r.json()
        # After backfill: must be 0
        assert data["comercial_sin_nif"] == 0, (
            f"Expected 0 after backfill, got {data['comercial_sin_nif']}"
        )


# --- 5) Vaciar módulo selectivo (dry_run only) ------------------------------
class TestVaciarModuloScopes:
    def test_scope_todo_dry_run(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/sii/vaciar-modulo",
            params={"dry_run": "true"},
            json={"confirmacion": "VACIAR", "scope": "todo"},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["scope"] == "todo"
        assert data["dry_run"] is True
        cols = data["colecciones_afectadas"]
        assert set(cols) == {"facturas_sii", "facturas_comercial",
                             "consultas", "jobs"}

    def test_scope_sii_dry_run(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/sii/vaciar-modulo",
            params={"dry_run": "true"},
            json={"confirmacion": "VACIAR", "scope": "sii"},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["scope"] == "sii"
        cols = data["colecciones_afectadas"]
        assert set(cols) == {"facturas_sii", "consultas"}
        assert "facturas_comercial" not in cols
        assert "jobs" not in cols

    def test_scope_comercial_dry_run(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/sii/vaciar-modulo",
            params={"dry_run": "true"},
            json={"confirmacion": "VACIAR", "scope": "comercial"},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["scope"] == "comercial"
        cols = data["colecciones_afectadas"]
        assert set(cols) == {"facturas_comercial"}
        assert "facturas_sii" not in cols
        assert "consultas" not in cols
        assert "jobs" not in cols

    def test_scope_invalid_returns_422(self, admin_session):
        r = admin_session.post(
            f"{BASE_URL}/api/admin/sii/vaciar-modulo",
            params={"dry_run": "true"},
            json={"confirmacion": "VACIAR", "scope": "foo"},
            timeout=30,
        )
        assert r.status_code == 422, (
            f"Expected 422 Pydantic Literal validation, got {r.status_code}: {r.text}"
        )
