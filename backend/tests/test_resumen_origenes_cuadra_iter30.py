"""Regression test iter30: `/comparativa/resumen-origenes` y
`/comparativa/totales` deben reportar exactamente los mismos totales
base/cuota por origen (en valor absoluto), para que los dos cuadros
del dashboard cuadren.

Bug reportado por el usuario:
    "Por qué estos dos cuadros resumen son diferentes? la base
    imponible al menos no es igual"
    - Cuadro A (resumen-origenes) SIGLO: base = -14.514.682,14
    - Cuadro B (totales/ColumnaTotales) SIGLO: base =  14.510.070,53
    (diff ≈ 4.611 €)

Causas encontradas:
    1) `resumen-origenes` sumaba `$base_imponible` (top-level) sin excluir
       líneas con tipo_impositivo=0.
    2) `resumen-origenes` no aplicaba el fallback canónico (importe_total
       cuando base+cuota=0) que sí aplica `totales`.

Fix (iter30):
    - Ambos usan `_com_base_neto` (excluye tipo_impositivo=0) más el
      fallback canónico `_com_base_final` (importe_total cuando base+
      cuota=0 e importe_total != 0).
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
def admin_session():
    import sys
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token, COOKIE_ACCESS  # noqa: WPS433

    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    user = db.users.find_one({"email": ADMIN_EMAIL})
    assert user, "Admin user no encontrado en BD"
    token = create_access_token(user["_id"], user["email"])

    s = requests.Session()
    s.cookies.set(
        COOKIE_ACCESS, token,
        domain="soap-factura-batch.preview.emergentagent.com",
    )
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    assert r.status_code == 200, f"Auth failed: {r.status_code}"
    return s


@pytest.mark.parametrize("nif", ["A74251836", "A95000295"])
def test_resumen_origenes_cuadra_con_totales(admin_session, nif):
    """Base y cuota por origen deben coincidir en valor absoluto entre
    /comparativa/totales y /comparativa/resumen-origenes."""
    r1 = admin_session.get(
        f"{BASE_URL}/api/comparativa/resumen-origenes",
        params={"nif_titular": nif}, timeout=90,
    )
    assert r1.status_code == 200, r1.text
    r2 = admin_session.get(
        f"{BASE_URL}/api/comparativa/totales",
        params={"nif_titular": nif}, timeout=90,
    )
    assert r2.status_code == 200, r2.text

    resumen = {o["origen"]: o for o in (r1.json().get("items") or [])}
    totales = r2.json().get("comercial_por_origen") or {}

    common = set(resumen.keys()) & set(totales.keys())
    assert common, f"No hay orígenes en común para NIF {nif}"

    for og in common:
        base_res = abs(float(resumen[og]["base_total"]))
        base_tot = abs(float(totales[og]["base"]))
        cuota_res = abs(float(resumen[og]["cuota_total"]))
        cuota_tot = abs(float(totales[og]["cuota"]))
        # Tolerancia = 1€ (redondeos de aggregation en volúmenes grandes)
        assert abs(base_res - base_tot) < 1.0, (
            f"NIF {nif} · {og}: base difiere entre endpoints: "
            f"resumen={base_res} vs totales={base_tot}"
        )
        assert abs(cuota_res - cuota_tot) < 1.0, (
            f"NIF {nif} · {og}: cuota difiere entre endpoints: "
            f"resumen={cuota_res} vs totales={cuota_tot}"
        )
