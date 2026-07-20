"""Regression test iter30: el listado principal `/comparativa` con
`only_diffs=true` DEBE incluir las facturas `solo_sii` (SII sin match
comercial), no sólo las de universo comercial.

Bug reportado por el usuario:
    "Por qué no se detectan diferencias si hay una del tanto en base
    imponible como en cuota?"
    - Filtro: F1+F2 + BASER + "Sólo con diferencias"
    - Resumen KPIs: Δ Base 65.541,80 € · Δ Cuota 13.763,69 € · Δ Canónico 79.305,49 €
    - Listado: 0 resultados / "Sin diferencias detectadas"

Causa:
    Fast-path pipeline (>50k docs) partía sólo de `facturas_comercial`,
    con lo que las 114 facturas F1/F2 en SII sin match comercial nunca
    aparecían en el listado, pese a contribuir al Δ Base/Cuota agregado.

Fix:
    `$unionWith` con `facturas_sii` (sub-pipeline que excluye los que sí
    tienen contraparte comercial) cuando `estado is None`.
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
    assert user, "Admin user no encontrado"
    token = create_access_token(user["_id"], user["email"])

    s = requests.Session()
    s.cookies.set(
        COOKIE_ACCESS, token,
        domain="soap-factura-batch.preview.emergentagent.com",
    )
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    assert r.status_code == 200
    return s


def test_only_diffs_incluye_solo_sii(admin_session):
    """Con estado=None y only_diffs=true, el total debe incluir las
    facturas solo_sii (equivalente al fast-path dedicado)."""
    NIF = "A74251836"
    TIPOS = "F1,F2"

    # Fast-path dedicado solo_sii (referencia)
    r_ss = admin_session.get(
        f"{BASE_URL}/api/comparativa",
        params={
            "nif_titular": NIF, "tipos_factura": TIPOS,
            "estado": "solo_sii", "limit": 1,
        }, timeout=60,
    )
    assert r_ss.status_code == 200
    solo_sii_n = int(r_ss.json().get("total") or 0)

    # Fast-path dedicado solo_comercial
    r_sc = admin_session.get(
        f"{BASE_URL}/api/comparativa",
        params={
            "nif_titular": NIF, "tipos_factura": TIPOS,
            "estado": "solo_comercial", "limit": 1,
        }, timeout=60,
    )
    assert r_sc.status_code == 200
    solo_com_n = int(r_sc.json().get("total") or 0)

    # Discrepancia
    r_d = admin_session.get(
        f"{BASE_URL}/api/comparativa",
        params={
            "nif_titular": NIF, "tipos_factura": TIPOS,
            "estado": "discrepancia", "limit": 1,
        }, timeout=60,
    )
    assert r_d.status_code == 200
    disc_n = int(r_d.json().get("total") or 0)

    esperado = solo_sii_n + solo_com_n + disc_n

    # Listado global con only_diffs=true
    r = admin_session.get(
        f"{BASE_URL}/api/comparativa",
        params={
            "nif_titular": NIF, "tipos_factura": TIPOS,
            "only_diffs": "true", "limit": 300,
        }, timeout=90,
    )
    assert r.status_code == 200
    data = r.json()
    total = int(data.get("total") or 0)

    assert total == esperado, (
        f"only_diffs total={total} debería igualar "
        f"solo_sii({solo_sii_n}) + solo_comercial({solo_com_n}) + "
        f"discrepancia({disc_n}) = {esperado}"
    )

    # Verifica que ALGUNOS items sean solo_sii (si los hay)
    if solo_sii_n > 0:
        estados = {i.get("estado") for i in (data.get("items") or [])}
        assert "solo_sii" in estados, (
            "Se esperan items con estado=solo_sii, sólo hay: " +
            str(estados)
        )


def test_estado_especifico_no_mezcla_solo_sii(admin_session):
    """Cuando el usuario filtra estado=discrepancia (o solo_comercial),
    el listado NO debe incluir solo_sii — el $unionWith sólo se activa
    con estado=None."""
    NIF = "A74251836"

    for filt in ("discrepancia", "solo_comercial", "coincide"):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa",
            params={
                "nif_titular": NIF, "tipos_factura": "F1,F2",
                "estado": filt, "limit": 20,
            }, timeout=60,
        )
        assert r.status_code == 200
        items = r.json().get("items") or []
        for it in items:
            assert it.get("estado") == filt, (
                f"Filtro estado={filt} devolvió estado="
                f"{it.get('estado')}"
            )
