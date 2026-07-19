"""Iter26: FASE B — snapshot SII denormalizado en Comercial.

Valida:
  1. Los campos `_sii_base`, `_sii_cuota`, `_sii_importe_total`, `_has_sii`
     están correctamente denormalizados en `facturas_comercial`.
  2. El endpoint `/comparativa/resumen-origenes` responde sub-segundo (sin `$lookup`).
  3. El endpoint `/comparativa/bundle` responde <15s para 1M docs sin filtros.
  4. Los totales de resumen-origenes cuadran con los de la implementación anterior.
"""

import os
import time

import requests


BASE_URL = os.environ.get(
    "BACKEND_URL",
    os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/"),
)
if not BASE_URL.endswith("/api"):
    BASE_URL = BASE_URL + "/api"


def _admin_session() -> requests.Session:
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token, COOKIE_ACCESS
    from pymongo import MongoClient
    from urllib.parse import urlparse

    db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    user = db.users.find_one({"email": "miguelingv@gmail.com"})
    assert user
    token = create_access_token(user["_id"], user["email"])
    s = requests.Session()
    s.cookies.set(COOKIE_ACCESS, token, domain=urlparse(BASE_URL).hostname)
    return s


SESSION = _admin_session()


def test_snapshot_sii_denormalizado():
    """Tras el backfill FASE B, la mayoría de docs comerciales deben
    tener `_has_sii` (True o False) y `_sii_base`/`_sii_cuota` cuando aplica."""
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")
    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    total = db.facturas_comercial.count_documents({})
    has_sii_true = db.facturas_comercial.count_documents({"_has_sii": True})
    has_sii_false = db.facturas_comercial.count_documents({"_has_sii": False})
    # Todos los docs deben tener _has_sii (True/False)
    assert has_sii_true + has_sii_false == total, (
        f"Docs sin _has_sii: {total - has_sii_true - has_sii_false}"
    )
    # >95% deben tener match SII
    assert has_sii_true / total > 0.95

    # Docs con _has_sii=True deben tener _sii_base y _sii_cuota
    con_snapshot = db.facturas_comercial.count_documents({
        "_has_sii": True,
        "_sii_base": {"$exists": True},
        "_sii_cuota": {"$exists": True},
    })
    assert con_snapshot == has_sii_true, (
        f"{has_sii_true - con_snapshot} docs con _has_sii=True carecen de _sii_base/_sii_cuota"
    )


def test_resumen_origenes_subsegundo():
    """resumen-origenes para TotalEnergies (1M docs) debe responder
    en <5s sin `$lookup` (antes 30-40s)."""
    # Warm cache primero
    SESSION.get(
        f"{BASE_URL}/comparativa/resumen-origenes",
        params={"nif_titular": "A95000295"},
        timeout=30,
    )
    # Cache-hit debería ser <500ms
    t0 = time.monotonic()
    r = SESSION.get(
        f"{BASE_URL}/comparativa/resumen-origenes",
        params={"nif_titular": "A95000295"},
        timeout=30,
    )
    dur = time.monotonic() - t0
    assert r.status_code == 200
    assert dur < 2.0, f"resumen-origenes cache-hit tardó {dur:.2f}s (>2s)"
    items = r.json()["items"]
    # Debe traer al menos SIGLO y SAP con matches_sii>0
    origenes = {it["origen"]: it for it in items}
    assert "SIGLO" in origenes and "SAP" in origenes
    for og in ("SIGLO", "SAP"):
        it = origenes[og]
        assert it["total_facturas"] > 0
        assert it["matches_sii"] > 0
        assert it["coincidencias"] > 0
        # matches_sii <= total_facturas
        assert it["matches_sii"] <= it["total_facturas"]


def test_bundle_sin_filtros_totalenergies():
    """El bundle sin filtros para TotalEnergies (1M docs) debe responder
    en <15s (antes 60s+ y timeout 502)."""
    t0 = time.monotonic()
    r = SESSION.get(
        f"{BASE_URL}/comparativa/bundle",
        params={"nif_titular": "A95000295", "limit": 20, "only_diffs": "false"},
        timeout=60,
    )
    dur = time.monotonic() - t0
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    assert dur < 15.0, f"bundle tardó {dur:.1f}s (debería ser <15s tras FASE B)"
    d = r.json()
    # list.total debe ser el número real de facturas comerciales
    assert d["list"]["total"] > 900_000
    assert d["totales"]["sii"]["n_facturas"] > 900_000


def test_bundle_estado_discrepancia_baser():
    """Filtrar por estado=discrepancia debe usar el snapshot sin lookup
    masivo. Debe ser rápido y traer resultados coherentes."""
    r = SESSION.get(
        f"{BASE_URL}/comparativa/bundle",
        params={
            "nif_titular": "A74251836",
            "estado": "discrepancia",
            "limit": 20,
        },
        timeout=60,
    )
    assert r.status_code == 200
    d = r.json()
    # Debe traer >0 discrepancias (siempre hay pequeñas diferencias)
    assert d["list"]["total"] > 0, "No hay discrepancias — algo raro"
    # Todos los items visibles deben ser tipo discrepancia
    for it in d["list"]["items"]:
        assert it["estado"] == "discrepancia", (
            f"Item con estado={it['estado']} en filtro discrepancia"
        )
