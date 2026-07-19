"""Iter27: Reconciliación por importe canónico.

Cubre facturas No Sujeta y desgloses asimétricos donde SII sólo declara
importe_total (sin base/cuota) y Comercial desglosa en base+cuota (o
viceversa). El importe canónico = base+cuota si != 0, else importe_total.
Si SII_canonical == Comercial_canonical (con inversión de signo aplicada),
la factura se marca como `coincide` con badge `reconciliada_por_importe_canonico`.
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


def test_diff_facturas_canonical_amount():
    """Verifica que diff_facturas usa el fallback canónico correctamente.

    iter28: prioridad `importe_total` > `base+cuota`. Cubre facturas con
    partes exentas donde importe_total ≠ base+cuota.
    """
    import sys
    sys.path.insert(0, "/app/backend")
    from factura_model import diff_facturas

    # Caso No Sujeta: SII sólo tiene importe, Comercial desglosa
    sii = {"base_imponible": None, "cuota_repercutida": None, "importe_total": 151.54}
    com = {
        "base_imponible": -151.01,
        "cuota_repercutida": -0.53,
        "importe_total": -151.54,
        "origen_comercial": "SIGLO",
    }
    cfg = {
        "campos_comparados": ["base_imponible", "cuota_repercutida"],
        "invertir_signo_por_origen": {"SIGLO": True},
    }
    d = diff_facturas(sii, com, cfg)
    real_diffs = {k: v for k, v in d.items() if not k.startswith("_")}
    assert not real_diffs, f"Debería reconciliarse por canónico. Diffs: {real_diffs}"
    assert "_reconciliada_por_importe_canonico" in d
    marker = d["_reconciliada_por_importe_canonico"]
    assert abs(marker["sii_canonical"] - 151.54) < 0.01
    assert abs(marker["comercial_canonical"] - 151.54) < 0.01


def test_diff_facturas_no_aplica_si_todos_los_campos_coinciden():
    """Cuando SII y Comercial tienen base+cuota completos y coinciden, la
    marca canónica NO debe aparecer (comportamiento normal)."""
    import sys
    sys.path.insert(0, "/app/backend")
    from factura_model import diff_facturas

    sii = {"base_imponible": 100, "cuota_repercutida": 21, "importe_total": 121}
    com = {
        "base_imponible": 100,
        "cuota_repercutida": 21,
        "importe_total": 121,
        "origen_comercial": "SAP",
    }
    cfg = {"campos_comparados": ["base_imponible", "cuota_repercutida"]}
    d = diff_facturas(sii, com, cfg)
    assert not d, f"Coincidencia total no debe generar diffs. Got: {d}"


def test_diff_facturas_no_reconcilia_si_no_cuadra():
    """Si el canónico tampoco cuadra, sigue siendo discrepancia."""
    import sys
    sys.path.insert(0, "/app/backend")
    from factura_model import diff_facturas

    sii = {"base_imponible": None, "cuota_repercutida": None, "importe_total": 200}
    com = {
        "base_imponible": -100,
        "cuota_repercutida": -0.5,
        "importe_total": None,
        "origen_comercial": "SIGLO",
    }
    cfg = {
        "campos_comparados": ["base_imponible", "cuota_repercutida"],
        "invertir_signo_por_origen": {"SIGLO": True},
    }
    d = diff_facturas(sii, com, cfg)
    # SII canonical = 200; Com canonical = 100.5 (invertido a -100.5, luego
    # abs(200 - (-100.5)) = 300.5 → NO cuadra)
    # → deben quedar diffs de base/cuota
    assert "_reconciliada_por_importe_canonico" not in d
    assert d, "Debería tener diferencias reales"


def test_endpoint_marca_factura_no_sujeta_como_coincide():
    """El endpoint /comparativa debe devolver estado=coincide y el flag
    reconciliada_por_importe_canonico=True para el caso del usuario:
    factura 1NSN260600000453 (No Sujeta, TotalEnergies).
    """
    r = SESSION.get(
        f"{BASE_URL}/comparativa",
        params={"num_serie": "1NSN260600000453", "only_diffs": "false"},
        timeout=60,
    )
    assert r.status_code == 200
    items = r.json().get("items", [])
    assert items, "Factura no encontrada"
    it = items[0]
    assert it["num_serie_factura"] == "1NSN260600000453"
    assert it["estado"] == "coincide", (
        f"Esperaba estado=coincide, got={it['estado']}"
    )
    assert it["reconciliada_por_importe_canonico"] is True


def test_totales_incluye_importe_canonico_en_sii():
    """iter27: el endpoint /comparativa/totales debe reflejar el importe
    canónico en el KPI SII cuando la factura es No Sujeta (sin desglose)."""
    r = SESSION.get(
        f"{BASE_URL}/comparativa/totales",
        params={"num_serie": "1NSN260600000453"},
        timeout=60,
    )
    assert r.status_code == 200
    d = r.json()
    # SII base debe ser 151.54 (importe_total), no 0
    assert abs(d["sii"]["base"] - 151.54) < 0.01, (
        f"SII base esperado 151.54 (canonico), got {d['sii']['base']}"
    )
    # Δ canónico debe ser ≈ 0 (la conciliación real cuadra)
    assert abs(d["diferencias"]["canonico"]) < 0.01, (
        f"Δ canónico esperado ≈0, got {d['diferencias']['canonico']}"
    )


def test_resumen_origenes_incluye_reconciliadas_canonicas():
    """Tras iter27, TotalEnergies debe tener MÁS coincidencias que las que
    tenía con el filtro estricto base+cuota (>1M en SIGLO)."""
    r = SESSION.get(
        f"{BASE_URL}/comparativa/resumen-origenes",
        params={"nif_titular": "A95000295"},
        timeout=60,
    )
    assert r.status_code == 200
    origenes = {it["origen"]: it for it in r.json()["items"]}
    # SIGLO tras iter27: coincidencias >= 1.000.000 (antes ~995k)
    assert origenes["SIGLO"]["coincidencias"] > 1_000_000, (
        f"SIGLO coincidencias={origenes['SIGLO']['coincidencias']} — "
        f"esperaba >1M tras iter27"
    )
