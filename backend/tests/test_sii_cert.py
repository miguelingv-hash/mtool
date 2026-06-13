"""Backend API tests for SII real/mock switch & certificate upload (iteration 2)."""
import os
import io
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://soap-factura-batch.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE}/api"


VALID = {
    "nif_titular": "B12345678",
    "nombre_titular": "Mi Empresa S.L.",
    "ejercicio": "2025",
    "periodo": "01",
    "nif_emisor": "A87654321",
    "nombre_emisor": "Proveedor SA",
    "num_serie_factura": "F2025-001",
    "fecha_expedicion": "15-01-2025",
    "entorno": "preproduccion",
}


# --- /api/sii/config -------------------------------------------------------
def test_sii_config_default_mock():
    r = requests.get(f"{API}/sii/config")
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ("default_mode", "server_cert_configured", "real_mode_available", "wsdl", "endpoints"):
        assert k in d
    assert d["default_mode"] == "mock"
    assert d["server_cert_configured"] is False
    assert d["real_mode_available"] is True
    assert "SuministroFactEmitidas.wsdl" in d["wsdl"]
    assert "preproduccion" in d["endpoints"] and "produccion" in d["endpoints"]


# --- consulta-unitaria JSON still returns sii_mode=mock --------------------
def test_consulta_unitaria_json_has_sii_mode_mock():
    r = requests.post(f"{API}/sii/consulta-unitaria", json=VALID)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("sii_mode") == "mock"


# --- consulta-unitaria-cert (multipart) ------------------------------------
def _form(extra=None):
    f = {k: (None, v) for k, v in VALID.items()}
    if extra:
        f.update(extra)
    return f


def test_consulta_unitaria_cert_no_cert_no_mode_is_mock():
    r = requests.post(f"{API}/sii/consulta-unitaria-cert", files=_form())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sii_mode"] == "mock"
    assert d["respuesta"]["estado_factura"] in [
        "Correcta", "AceptadaConErrores", "Anulada", "NoRegistrada"
    ]


def test_consulta_unitaria_cert_explicit_mode_mock():
    r = requests.post(
        f"{API}/sii/consulta-unitaria-cert",
        files=_form({"mode": (None, "mock")}),
    )
    assert r.status_code == 200, r.text
    assert r.json()["sii_mode"] == "mock"


def test_consulta_unitaria_cert_invalid_pkcs12_returns_400():
    files = _form({"cert_password": (None, "wrongpass")})
    files["certificate"] = ("bad.pfx", b"not-a-real-pkcs12", "application/x-pkcs12")
    r = requests.post(f"{API}/sii/consulta-unitaria-cert", files=files)
    assert r.status_code == 400, r.text
    body = r.json()
    detail = body.get("detail", "")
    assert "PKCS#12" in detail, detail


def test_consulta_unitaria_cert_missing_required_field():
    bad = dict(VALID)
    bad.pop("nif_titular")
    files = {k: (None, v) for k, v in bad.items()}
    r = requests.post(f"{API}/sii/consulta-unitaria-cert", files=files)
    assert r.status_code in (400, 422), r.text


# --- consulta-batch with sii_mode ------------------------------------------
CSV = (
    "nif_titular;nombre_titular;ejercicio;periodo;nif_emisor;nombre_emisor;num_serie_factura;fecha_expedicion\n"
    "B12345678;Mi Empresa;2025;01;A87654321;Prov;F2025-101;15-01-2025\n"
    "B12345678;Mi Empresa;2025;01;A87654321;Prov;F2025-102;16-01-2025\n"
)


def test_batch_no_cert_returns_sii_mode_mock():
    files = {"file": ("data.csv", CSV.encode(), "text/csv")}
    r = requests.post(f"{API}/sii/consulta-batch", files=files)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sii_mode"] == "mock"
    assert d["total"] == 2
    for reg in d["registros"]:
        assert reg["sii_mode"] == "mock"


def test_batch_invalid_pkcs12_returns_400():
    files = {
        "file": ("data.csv", CSV.encode(), "text/csv"),
        "certificate": ("bad.pfx", b"not-a-real-pkcs12", "application/x-pkcs12"),
    }
    data = {"cert_password": "wrong"}
    r = requests.post(f"{API}/sii/consulta-batch", files=files, data=data)
    assert r.status_code == 400, r.text
    assert "PKCS#12" in r.json().get("detail", "")


# --- ConsultaRecord persisted with sii_mode --------------------------------
def test_record_persisted_with_sii_mode_field():
    created = requests.post(f"{API}/sii/consulta-unitaria", json=VALID).json()
    cid = created["id"]
    r = requests.get(f"{API}/sii/consultas/{cid}")
    assert r.status_code == 200
    assert r.json().get("sii_mode") == "mock"
