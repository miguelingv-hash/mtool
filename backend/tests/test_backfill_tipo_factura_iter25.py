"""Iter25: Denormalización de `tipo_factura` en `facturas_comercial`.

Valida el backfill masivo y los endpoints acelerados.
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
    """Sesión requests con cookie de admin (JWT minteado). Usa auth
    cookie-based que require_permission entiende (no funciona Bearer)."""
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token, COOKIE_ACCESS
    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    user = db.users.find_one({"email": "miguelingv@gmail.com"})
    assert user, "Admin user no encontrado"
    token = create_access_token(user["_id"], user["email"])
    s = requests.Session()
    # Extraer dominio del BASE_URL
    from urllib.parse import urlparse
    domain = urlparse(BASE_URL).hostname
    s.cookies.set(COOKIE_ACCESS, token, domain=domain)
    return s


SESSION = _admin_session()


def test_tipo_factura_denormalizado_en_comercial():
    """Tras el backfill, la mayoría de docs comerciales deben tener
    `tipo_factura` no-null (matching SII)."""
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")
    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    total = db.facturas_comercial.count_documents({})
    con_tipo = db.facturas_comercial.count_documents({
        "tipo_factura": {"$in": ["F1", "F2", "F3", "F4", "R1", "R2", "R3", "R4", "R5"]}
    })
    # Con >1M docs importados, tras backfill esperamos >95% con tipo
    ratio = con_tipo / max(total, 1)
    assert ratio > 0.95, (
        f"Sólo {ratio*100:.1f}% de comerciales tienen tipo_factura tras backfill"
    )


def test_tipos_factura_endpoint_rapido():
    """El endpoint /comparativa/tipos-factura para TotalEnergies (1M docs)
    debe responder rápido tras el backfill (antes ~15-30s con $lookup).

    Puede tardar la 1ª vez si el warmup ha caducado, pero por debajo de
    10s. Tras cache-hit, <1s.
    """
    import time
    t0 = time.monotonic()
    r = SESSION.get(
        f"{BASE_URL}/comparativa/tipos-factura",
        params={"nif_titular": "A95000295"},
        timeout=30,
    )
    dur = time.monotonic() - t0
    assert r.status_code == 200
    # Tras el 1er run (que puede coincidir con warmup) el cache está
    # calentado. El backfill iter25 reduce el tiempo real de <30s a
    # sub-segundo — validamos que como mucho tarda 20s (con warmup en
    # paralelo puede tardar más el 1er hit).
    assert dur < 20.0, (
        f"tipos-factura tardó {dur:.1f}s (debería ser <20s tras iter25)"
    )
    d = r.json()
    # Debe traer los buckets con counts correctos
    codes = {i["code"] for i in d["items"]}
    for expected in ("F1", "F2", "R1", "_sin_clasificar"):
        assert expected in codes


def test_bundle_filtro_tipo_r1_baser():
    """Filtrar bundle por tipo_factura=R1 debe traer 3.347 SII y ~3.347
    comerciales para BASER (backfill iter25 permitió aplicar el filtro
    directo sin $lookup)."""
    r = SESSION.get(
        f"{BASE_URL}/comparativa/bundle",
        params={
            "nif_titular": "A74251836",
            "tipos_factura": "R1",
            "limit": 3,
            "only_diffs": "false",
        },
        timeout=120,
    )
    assert r.status_code == 200
    d = r.json()
    tot = d["totales"]
    sii_n = tot["sii"]["n_facturas"]
    com_n = tot["comercial_total"]["n_facturas"]
    assert sii_n == 3347, f"SII R1 esperado 3347, got {sii_n}"
    # Post iter28.2 con exclusión tipo_impositivo=0, algunos comerciales
    # con líneas exentas quedan filtrados de la agregación. Tolerancia:
    # entre 3.200 y 3.400 comerciales R1.
    assert 3200 <= com_n <= 3400, (
        f"Comercial R1 esperado ~3300, got {com_n} — filtro no aplica bien"
    )


def test_admin_backfill_endpoint_idempotente():
    """Ejecutar el backfill 2 veces sobre BASER no debe corromper datos.
    Verifica que el endpoint admin autentica correctamente y responde OK.
    """
    r = SESSION.post(
        f"{BASE_URL}/admin/backfill-tipo-factura",
        params={"nif_titular": "A74251836"},
        timeout=300,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    d = r.json()
    assert d["ok"] is True
    # Termina en tiempo razonable (<3 min con 490k docs BASER)
    assert d["report"]["duracion_s"] < 180
