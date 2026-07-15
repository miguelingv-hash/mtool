"""Regression test for bug fix: fast-path estado=solo_comercial in
/api/comparativa/bundle was returning 0 or all comercials instead of
excluding those with SII match.

Validates:
  - GET /api/comparativa/bundle?nif=A74251836&estado=solo_comercial returns
    a list.total that matches the count of comerciales sin match SII.
  - Each item has estado=solo_comercial.
  - No returned num_serie_factura exists in facturas_sii for the same nif.
  - Mathematical coherence: pipeline count in Mongo matches API total.
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
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    assert r.status_code == 200, f"Auth bypass falló: {r.status_code}"
    return s


def _mongo_solo_com_count(db, nif: str) -> int:
    """Cuenta num_serie_factura de facturas_comercial (nif) que NO están
    en facturas_sii (nif)."""
    sii_keys = set(
        d["num_serie_factura"]
        for d in db.facturas_sii.find(
            {"nif_titular": nif}, {"_id": 0, "num_serie_factura": 1}
        )
    )
    total = 0
    for d in db.facturas_comercial.find(
        {"nif_titular": nif}, {"_id": 0, "num_serie_factura": 1}
    ):
        if d["num_serie_factura"] not in sii_keys:
            total += 1
    return total


class TestSoloComercialA74251836:
    NIF = "A74251836"

    def test_bundle_solo_comercial_total_no_es_cero(self, admin_session):
        """El bug reportado: devolvía 0 en vez del recuento real."""
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": self.NIF, "estado": "solo_comercial",
                    "skip": 0, "limit": 10},
            timeout=180,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:400]}"
        body = r.json()
        assert "list" in body
        total = body["list"]["total"]
        assert total > 0, (
            f"BUG: solo_comercial devuelve total=0 para {self.NIF}; "
            "debería ser el recuento de comerciales sin match SII."
        )
        # Sanity: no debe devolver TODAS las comerciales
        assert total < 487_749, (
            f"Sospechoso: total={total} == universo comercial completo; "
            "el fast-path no está excluyendo los que sí tienen match SII."
        )

    def test_bundle_items_son_solo_comercial(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": self.NIF, "estado": "solo_comercial",
                    "skip": 0, "limit": 20},
            timeout=180,
        )
        assert r.status_code == 200
        items = r.json()["list"]["items"]
        assert len(items) > 0
        for it in items:
            # No debe tener contraparte SII
            sii = it.get("sii")
            assert not sii or not sii.get("num_serie_factura"), (
                f"Item {it.get('num_serie_factura')} tiene sii poblado "
                "pese a estado=solo_comercial."
            )

    def test_items_no_existen_en_sii(self, admin_session, db):
        """Verifica que los num_serie_factura devueltos NO están en
        facturas_sii con el mismo nif."""
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": self.NIF, "estado": "solo_comercial",
                    "skip": 0, "limit": 30},
            timeout=180,
        )
        assert r.status_code == 200
        items = r.json()["list"]["items"]
        ns_list = [it["num_serie_factura"] for it in items if it.get("num_serie_factura")]
        assert ns_list, "Sin items devueltos"
        hits = list(db.facturas_sii.find(
            {"nif_titular": self.NIF, "num_serie_factura": {"$in": ns_list}},
            {"_id": 0, "num_serie_factura": 1},
        ))
        assert not hits, (
            f"BUG: {len(hits)} num_serie_factura solo_comercial también "
            f"aparecen en facturas_sii: {[h['num_serie_factura'] for h in hits[:5]]}"
        )

    def test_coherencia_matematica_con_mongo(self, admin_session, db):
        """El total devuelto por el bundle debe coincidir (±1%) con el
        conteo directo en Mongo."""
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": self.NIF, "estado": "solo_comercial",
                    "skip": 0, "limit": 1},
            timeout=180,
        )
        assert r.status_code == 200
        api_total = r.json()["list"]["total"]

        mongo_total = _mongo_solo_com_count(db, self.NIF)
        # Tolerancia 1%
        tol = max(50, int(mongo_total * 0.01))
        assert abs(api_total - mongo_total) <= tol, (
            f"Discrepancia: API={api_total} vs Mongo={mongo_total} "
            f"(tol=±{tol})"
        )


class TestSoloComercialA95000295:
    NIF = "A95000295"

    def test_bundle_solo_comercial_total_positivo(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": self.NIF, "estado": "solo_comercial",
                    "skip": 0, "limit": 10},
            timeout=180,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:400]}"
        total = r.json()["list"]["total"]
        assert total > 0
        assert total < 1_022_176  # no todo el universo comercial


class TestCoincideA74251836:
    NIF = "A74251836"

    def test_bundle_coincide_items_tienen_ambas_partes(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/bundle",
            params={"nif_titular": self.NIF, "estado": "coincide",
                    "skip": 0, "limit": 15},
            timeout=180,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:400]}"
        body = r.json()
        assert body["list"]["total"] > 0
        for it in body["list"]["items"]:
            sii = it.get("sii") or {}
            com = it.get("comercial") or {}
            assert sii.get("num_serie_factura"), f"coincide sin SII: {it}"
            assert com.get("num_serie_factura"), f"coincide sin COM: {it}"
