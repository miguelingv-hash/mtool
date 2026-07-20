"""Regression test iter29: fast-path estado=solo_sii debe aplicar el
mismo `filtro_com` (excluir_comercial_base_cero, tipo_factura, nif,
ejercicio, periodo) que el resumen.

Bug reportado por el usuario:
    "Parece que hay una factura de diferencia que debería estar solo en
    SII pero el mensaje que aparece es que no hay nada en solo SII para
    el tipo F2 de BASER."

Causa raíz:
    El resumen aplica `excluir_comercial_base_cero=True` → 1 comercial
    F2 BASER con base=0 excluida del universo → banner sugiere "1 solo
    en SII". Pero el fast-path `solo_sii` hacía `$lookup` sin filtro,
    veía esa comercial como "match válido" y decía 0 orfanas.

Fix:
    El `$lookup` del fast-path solo_sii ahora ejecuta `filtro_com`
    completo dentro del sub-pipeline (excepto num_serie_factura, que se
    enforce via $expr).
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
NIF_BASER = "A74251836"


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


def test_solo_sii_respeta_excluir_base_cero(db, admin_session):
    """El fast-path solo_sii debe considerar como "orfano" un SII cuyo
    match comercial fue excluido del universo por config
    excluir_comercial_base_cero=True."""

    # Precondición: config debe tener excluir_comercial_base_cero=True.
    cfg = db.comparativa_config.find_one({}) or {}
    assert cfg.get("excluir_comercial_base_cero") is True, (
        "Test asume excluir_comercial_base_cero=True. Config actual: "
        f"{cfg.get('excluir_comercial_base_cero')}"
    )

    # SII F2 BASER: total
    sii_n = db.facturas_sii.count_documents({
        "tipo_factura": "F2", "nif_titular": NIF_BASER,
    })

    # Comercial F2 BASER con base=0 (excluida por config)
    com_zero = db.facturas_comercial.count_documents({
        "tipo_factura": "F2", "nif_titular": NIF_BASER,
        "$or": [
            {"base_imponible": 0},
            {"base_imponible": 0.0},
            {"base_imponible": None},
        ],
    })

    # Con base != 0 (universo real que se usa en el resumen)
    com_real = db.facturas_comercial.count_documents({
        "tipo_factura": "F2", "nif_titular": NIF_BASER,
        "base_imponible": {"$nin": [0, 0.0, None]},
    })

    # Sanity: hay algún caso con base=0 para poder validar el fix.
    if com_zero == 0:
        pytest.skip(
            "No hay facturas comercial F2 BASER con base=0 → nada que "
            "validar. Skipping."
        )

    # Llamada al API con estado=solo_sii + filtro F2 + BASER
    r = admin_session.get(
        f"{BASE_URL}/api/comparativa",
        params={
            "estado": "solo_sii",
            "nif_titular": NIF_BASER,
            "tipos_factura": "F2",
            "limit": 50,
        },
        timeout=60,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    total_solo_sii = int(data.get("total") or 0)

    # Expectativa: al excluir com_zero comerciales por base=0, ese mismo
    # número de SII debe aparecer como "solo en SII" (asumiendo que
    # cada SII tenía su match comercial que quedó excluido).
    # Cota superior: sii_n - com_real (upper bound teórico).
    upper = sii_n - com_real
    assert total_solo_sii >= com_zero, (
        f"Esperado >= {com_zero} solo_sii (comerciales excluidas por "
        f"base=0), obtenido {total_solo_sii}. sii={sii_n}, "
        f"com_real={com_real}, com_zero={com_zero}"
    )
    assert total_solo_sii <= max(upper, com_zero), (
        f"total_solo_sii ({total_solo_sii}) supera la cota superior "
        f"({max(upper, com_zero)})"
    )

    # Verifica que cada item efectivamente NO tiene comercial válida
    for item in data.get("items", [])[:10]:
        assert item.get("estado") == "solo_sii"
        assert item.get("en_sii") is True
        assert item.get("en_comercial") is False
        ns = item.get("num_serie_factura")
        # Comercial con ese num_serie DEBE tener base=0 o no existir.
        com_docs = list(db.facturas_comercial.find({
            "num_serie_factura": ns, "nif_titular": NIF_BASER,
        }, {"base_imponible": 1, "tipo_factura": 1}))
        for c in com_docs:
            # O bien base_imponible es 0/null → excluida
            # O bien tipo_factura != F2 (no cumpliría el filtro)
            base = c.get("base_imponible")
            tipo = c.get("tipo_factura")
            excluded = (
                base in (0, 0.0, None) or tipo != "F2"
            )
            assert excluded, (
                f"num_serie {ns} tiene comercial válida "
                f"(base={base}, tipo={tipo}) pero se reportó como "
                "solo_sii"
            )


def test_bundle_consistente_matches_num_serie_vs_solo_sii(admin_session):
    """El total de fast-path solo_sii debe cuadrar con la diferencia
    entre `sii_n` y `matches_num_serie` del bundle.

    Cota fuerte: solo_sii = sii_n − matches_num_serie
    (todos los orígenes con `_has_sii=True` cuentan como match).
    """
    # Bundle F2 BASER
    r = admin_session.get(
        f"{BASE_URL}/api/comparativa/bundle",
        params={
            "nif_titular": NIF_BASER,
            "tipos_factura": "F2",
            "limit": 1,
        },
        timeout=90,
    )
    assert r.status_code == 200, r.text
    bundle = r.json()
    totales = bundle.get("totales") or {}
    diff = totales.get("diferencias") or {}
    sii_n = int((totales.get("sii") or {}).get("n_facturas") or 0)
    matches = int(diff.get("matches_num_serie") or 0)
    esperado_solo_sii = max(sii_n - matches, 0)

    # Ahora consulta el fast-path solo_sii
    r2 = admin_session.get(
        f"{BASE_URL}/api/comparativa",
        params={
            "estado": "solo_sii",
            "nif_titular": NIF_BASER,
            "tipos_factura": "F2",
            "limit": 1,
        },
        timeout=60,
    )
    assert r2.status_code == 200, r2.text
    total_solo_sii = int((r2.json()).get("total") or 0)

    assert total_solo_sii == esperado_solo_sii, (
        f"Inconsistencia: bundle sugiere {esperado_solo_sii} solo_sii "
        f"(sii={sii_n} - matches={matches}), pero fast-path devuelve "
        f"{total_solo_sii}"
    )
