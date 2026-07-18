"""Test iter23: filtro tipos_factura aplica también al lado comercial
vía SII match (por num_serie_factura único).

Bug reportado: al filtrar por tipos_factura=R1, la tarjeta Resumen
Conciliación seguía mostrando 489.536 comerciales (todas) porque el
comercial no tiene el campo `tipo_factura` — sólo el SII. La solución
es cruzar por num_serie e incluir sólo los comerciales cuyo match SII
sea del tipo seleccionado (los `solo_comercial` sin match sólo cuentan
si el bucket `_sin_clasificar` está marcado).
"""

import os
import requests

BASE_URL = os.environ.get(
    "BACKEND_URL",
    os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/"),
)
if not BASE_URL.endswith("/api"):
    BASE_URL = BASE_URL + "/api"


def _jwt() -> str:
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token
    return create_access_token("admin-seed", "miguelingv@gmail.com")


HDR = {"Authorization": f"Bearer {_jwt()}"}


def test_comercial_filtrado_por_tipo_sii_r1():
    """Con tipos_factura=R1 y sin `_sin_clasificar`, el comercial_total
    debe reflejar SÓLO las facturas cuyo match SII es R1, no todas."""
    r = requests.get(
        f"{BASE_URL}/comparativa/bundle",
        params={
            "nif_titular": "A74251836",
            "tipos_factura": "R1",
            "limit": 3,
        },
        headers=HDR,
        timeout=180,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    d = r.json()
    sii_n = d["totales"]["sii"]["n_facturas"]
    com_n = d["totales"]["comercial_total"]["n_facturas"]
    # Como el 100% de comerciales de A74251836 son SIGLO/SAP y todos
    # tienen match SII, com_n debe ser aprox igual a sii_n (±unos pocos
    # por facturas donde no hay contraparte comercial).
    assert com_n < 10_000, (
        f"comercial_total sigue trayendo el universo entero ({com_n}) — "
        "el filtro tipo_factura no está aplicando al comercial via lookup"
    )
    # Adicional: el comercial NO debe superar al SII en un margen loco.
    assert com_n <= sii_n + 100, (
        f"comercial ({com_n}) >> sii ({sii_n}) es sospechoso — revisa el filtro"
    )


def test_comercial_filtrado_por_tipo_sin_clasificar_incluye_solo_com():
    """Con `_sin_clasificar` incluido, el comercial_total añade los
    solo_comercial (facturas sin match SII)."""
    # Sin _sin_clasificar
    r1 = requests.get(
        f"{BASE_URL}/comparativa/bundle",
        params={"nif_titular": "A74251836", "tipos_factura": "R1", "limit": 1},
        headers=HDR, timeout=180,
    )
    # Con _sin_clasificar
    r2 = requests.get(
        f"{BASE_URL}/comparativa/bundle",
        params={
            "nif_titular": "A74251836",
            "tipos_factura": "R1,_sin_clasificar",
            "limit": 1,
        },
        headers=HDR, timeout=180,
    )
    n_sin = r1.json()["totales"]["comercial_total"]["n_facturas"]
    n_con = r2.json()["totales"]["comercial_total"]["n_facturas"]
    assert n_con >= n_sin, (
        f"Incluir _sin_clasificar debe sumar solo_comercial "
        f"(sin={n_sin} vs con={n_con})"
    )
