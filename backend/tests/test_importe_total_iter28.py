"""Iter28: Priorización de importe_total en canonical + backfill.

Cubre:
  1. `_canonical_amount` prioriza importe_total sobre base+cuota → cubre
     facturas con partes exentas donde importe_total ≠ base+cuota.
  2. Auto-cálculo de importe_total en imports comerciales cuando falta.
  3. Endpoint /comparativa marca correctamente el caso del usuario
     (1NSN260600001319) como coincide.
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


def test_canonical_prioriza_importe_total():
    """Con desglose + importe_total distintos, canonical = importe_total."""
    import sys
    sys.path.insert(0, "/app/backend")
    from factura_model import _canonical_amount

    # Caso con parte exenta: base+cuota=111,49 pero importe_total=113,48
    doc = {"base_imponible": 92.14, "cuota_repercutida": 19.35, "importe_total": 113.48}
    assert abs(_canonical_amount(doc) - 113.48) < 0.01

    # Sin importe_total → fallback a base+cuota
    doc = {"base_imponible": 100, "cuota_repercutida": 21, "importe_total": None}
    assert abs(_canonical_amount(doc) - 121) < 0.01

    # Todo a 0/None → 0
    doc = {"base_imponible": None, "cuota_repercutida": None, "importe_total": None}
    assert _canonical_amount(doc) == 0


def test_factura_con_parte_exenta_coincide():
    """Caso del usuario iter28: factura 1NSN260600001319 con parte exenta
    de 1,99€. Antes = discrepancia, ahora = coincide.

    Con iter28.2 (exclusión inline de líneas tipo_impositivo=null), esta
    factura coincide por CAMPOS directamente (post-exclusión base+cuota
    cuadran), no por canonical. La marca `reconciliada_por_importe_canonico`
    puede ser False.
    """
    r = SESSION.get(
        f"{BASE_URL}/comparativa",
        params={"num_serie": "1NSN260600001319", "only_diffs": "false"},
        timeout=60,
    )
    assert r.status_code == 200
    items = r.json().get("items", [])
    assert items, "Factura no encontrada"
    it = items[0]
    assert it["estado"] == "coincide", (
        f"Esperaba coincide (parte exenta), got {it['estado']}"
    )


def test_importe_total_backfill_cubre_mayoria_comerciales():
    """Tras el backfill iter28, casi todos los comerciales con detalle_iva
    deben tener importe_total. Sólo los que suman 0 quedan sin."""
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")
    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    con_detalle_sin_importe = db.facturas_comercial.count_documents({
        "detalle_iva.0": {"$exists": True},
        "$or": [
            {"importe_total": None},
            {"importe_total": 0},
            {"importe_total": {"$exists": False}},
        ],
    })
    # Deberían quedar <10k tras el backfill (todos con detalle que suma 0)
    assert con_detalle_sin_importe < 10_000, (
        f"{con_detalle_sin_importe} comerciales aún sin importe_total"
    )
