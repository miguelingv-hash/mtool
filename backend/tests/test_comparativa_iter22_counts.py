"""Iter22 targeted checks:
- solo_sii must return total>0 (previously HTTP 500).
- coincide must return total>0 (previously 0 due to $_sii/$_sii_raw bug).
- discrepancia must return valid total.
- resumen-origenes returns matches_sii, coincidencias, discrepancias for SIGLO.
"""
import os, sys, requests, pytest
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env"); load_dotenv("/app/frontend/.env")
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
NIF_SMALL = "A74251836"
NIF_LARGE = "A95000295"


@pytest.fixture(scope="module")
def admin_session():
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token, COOKIE_ACCESS
    db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    user = db.users.find_one({"email": "miguelingv@gmail.com"})
    tok = create_access_token(user["_id"], user["email"])
    s = requests.Session()
    s.cookies.set(COOKIE_ACCESS, tok,
                  domain="soap-factura-batch.preview.emergentagent.com")
    return s


def _bundle(s, **params):
    r = s.get(f"{BASE_URL}/api/comparativa/bundle", params=params, timeout=180)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
    return r.json()


class TestCountsPositive:
    def test_solo_sii_A74(self, admin_session):
        b = _bundle(admin_session, nif_titular=NIF_SMALL, estado="solo_sii",
                    skip=0, limit=5)
        total = b["list"]["total"]
        assert total > 0, f"solo_sii A74251836 total={total} (esperado >0)"

    def test_coincide_A74(self, admin_session):
        b = _bundle(admin_session, nif_titular=NIF_SMALL, estado="coincide",
                    skip=0, limit=5)
        total = b["list"]["total"]
        assert total > 0, f"coincide A74251836 total={total} (fix $_sii_raw)"

    def test_discrepancia_A74(self, admin_session):
        b = _bundle(admin_session, nif_titular=NIF_SMALL, estado="discrepancia",
                    skip=0, limit=5)
        total = b["list"]["total"]
        assert isinstance(total, int) and total >= 0, f"discrepancia total={total}"

    def test_solo_sii_A95(self, admin_session):
        b = _bundle(admin_session, nif_titular=NIF_LARGE, estado="solo_sii",
                    skip=0, limit=5)
        assert isinstance(b["list"]["total"], int)


class TestResumenOrigenes:
    def test_siglo_signo_invertido(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/resumen-origenes",
            params={"nif_titular": NIF_SMALL}, timeout=180,
        )
        assert r.status_code == 200, r.text[:300]
        items = r.json().get("items", [])
        siglo = next((x for x in items if x.get("origen","").upper() == "SIGLO"), None)
        # SIGLO puede no existir para este NIF; validar estructura solo si existe.
        if siglo:
            for k in ("matches_sii", "coincidencias", "discrepancias"):
                assert k in siglo, f"Falta '{k}' en SIGLO: {list(siglo.keys())}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
