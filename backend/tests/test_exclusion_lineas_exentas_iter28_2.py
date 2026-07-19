"""Iter28.2: exclusión tipo_impositivo=0/null inline en aggregation.

Alinea el aggregation (fast-path list + resumen-origenes) con la lógica de
Python `diff_facturas` cuando `excluir_comercial_tipo_iva_cero=True`. Sin
este fix, el filtro `only_diffs=true` incluía facturas que la UI luego
mostraba como "Coincide" (facturas con líneas exentas en SAP que
compensaban el desglose vs SII).
"""

import os

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


def test_facturas_con_lineas_exentas_no_aparecen_en_only_diffs():
    """Regresión: las 3 facturas SAP con líneas exentas (tipo=null) que
    la UI marcaba como 'Coincide' NO deben aparecer en `only_diffs=true`."""
    for ns in ("26TAFEN000006225", "26TAANN000009407", "26TAASN000008378"):
        r = SESSION.get(
            f"{BASE_URL}/comparativa",
            params={"num_serie": ns, "only_diffs": "true", "limit": 5},
            timeout=60,
        )
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 0, (
            f"Factura {ns} aparece en only_diffs=true (total={d['total']}) "
            "cuando la UI la marca 'Coincide' — inconsistencia aggregation vs "
            "diff_facturas Python"
        )


def test_facturas_con_lineas_exentas_si_aparecen_sin_filtro():
    """Sanity check: las mismas facturas SÍ deben aparecer cuando pedimos
    todas (only_diffs=false) y con estado=coincide."""
    for ns in ("26TAFEN000006225", "26TAANN000009407", "26TAASN000008378"):
        r = SESSION.get(
            f"{BASE_URL}/comparativa",
            params={"num_serie": ns, "only_diffs": "false", "limit": 5},
            timeout=60,
        )
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 1
        assert d["items"][0]["estado"] == "coincide"
