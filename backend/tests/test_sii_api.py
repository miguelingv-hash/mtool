"""Backend API tests for SII Consulta service."""
import os
import io
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://soap-factura-batch.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"


VALID_PAYLOAD = {
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


# --- Root endpoint
def test_root():
    r = requests.get(f"{API}/")
    assert r.status_code == 200
    data = r.json()
    assert "wsdl" in data
    assert "SuministroFactEmitidas.wsdl" in data["wsdl"]
    assert "endpoints" in data
    assert "preproduccion" in data["endpoints"]
    assert "produccion" in data["endpoints"]


# --- Unitary consult
def test_consulta_unitaria_ok():
    r = requests.post(f"{API}/sii/consulta-unitaria", json=VALID_PAYLOAD)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "id" in data and "timestamp" in data
    assert data["entrada"]["nif_emisor"] == "A87654321"
    resp = data["respuesta"]
    assert resp["estado_factura"] in ["Correcta", "AceptadaConErrores", "Anulada", "NoRegistrada"]
    assert resp["wsdl"].endswith(".wsdl")
    assert "endpoint" in resp
    assert "soap_request_xml" in data and "ConsultaLRFacturasEmitidas" in data["soap_request_xml"]
    assert "soap_response_xml" in data and "RespuestaConsultaLRFactEmitidas" in data["soap_response_xml"]


def test_determinism():
    r1 = requests.post(f"{API}/sii/consulta-unitaria", json=VALID_PAYLOAD).json()
    r2 = requests.post(f"{API}/sii/consulta-unitaria", json=VALID_PAYLOAD).json()
    assert r1["respuesta"]["estado_factura"] == r2["respuesta"]["estado_factura"]


def test_invalid_nif_too_short():
    bad = {**VALID_PAYLOAD, "nif_titular": "ABC"}
    r = requests.post(f"{API}/sii/consulta-unitaria", json=bad)
    assert r.status_code == 422


def test_invalid_periodo():
    bad = {**VALID_PAYLOAD, "periodo": "13"}
    r = requests.post(f"{API}/sii/consulta-unitaria", json=bad)
    assert r.status_code == 422


def test_invalid_fecha_yyyy_mm_dd():
    bad = {**VALID_PAYLOAD, "fecha_expedicion": "2025-01-15"}
    r = requests.post(f"{API}/sii/consulta-unitaria", json=bad)
    assert r.status_code == 422


# --- Batch
CSV_SEMI = (
    "nif_titular;nombre_titular;ejercicio;periodo;nif_emisor;nombre_emisor;num_serie_factura;fecha_expedicion\n"
    "B12345678;Mi Empresa S.L.;2025;01;A87654321;Prov SA;F2025-001;15-01-2025\n"
    "B12345678;Mi Empresa S.L.;2025;01;A87654321;Prov SA;F2025-002;20-01-2025\n"
    "B12345678;Mi Empresa S.L.;2025;01;A87654321;Prov SA;F2025-003;25-01-2025\n"
    "INVALID;X;abcd;01;A87654321;Prov;F;15-01-2025\n"
)

CSV_COMMA = (
    "nif_titular,nombre_titular,ejercicio,periodo,nif_emisor,nombre_emisor,num_serie_factura,fecha_expedicion\n"
    "B12345678,Mi Empresa,2025,01,A87654321,Prov,F2025-010,15-01-2025\n"
    "B12345678,Mi Empresa,2025,01,A87654321,Prov,F2025-011,16-01-2025\n"
)


def test_batch_semicolon():
    files = {"file": ("data.csv", CSV_SEMI.encode(), "text/csv")}
    r = requests.post(f"{API}/sii/consulta-batch", files=files, data={"entorno": "preproduccion"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 3
    assert d["errores_validacion"] == 1
    assert "batch_id" in d
    assert len(d["registros"]) == 3


def test_batch_comma():
    files = {"file": ("data.csv", CSV_COMMA.encode(), "text/csv")}
    r = requests.post(f"{API}/sii/consulta-batch", files=files)
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_batch_bad_headers():
    bad_csv = "foo;bar\n1;2\n"
    files = {"file": ("data.csv", bad_csv.encode(), "text/csv")}
    r = requests.post(f"{API}/sii/consulta-batch", files=files)
    assert r.status_code == 400


def test_batch_not_csv():
    files = {"file": ("data.txt", b"hello", "text/plain")}
    r = requests.post(f"{API}/sii/consulta-batch", files=files)
    assert r.status_code == 400


# --- Listing / get
def test_list_consultas_pagination_and_filter():
    r = requests.get(f"{API}/sii/consultas", params={"skip": 0, "limit": 5})
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) <= 5

    r2 = requests.get(f"{API}/sii/consultas", params={"modo": "unitaria", "limit": 10})
    assert r2.status_code == 200
    assert all(it["modo"] == "unitaria" for it in r2.json())


def test_get_consulta_by_id():
    created = requests.post(f"{API}/sii/consulta-unitaria", json=VALID_PAYLOAD).json()
    cid = created["id"]
    r = requests.get(f"{API}/sii/consultas/{cid}")
    assert r.status_code == 200
    assert r.json()["id"] == cid


def test_get_consulta_404():
    r = requests.get(f"{API}/sii/consultas/non-existent-id-zzz")
    assert r.status_code == 404


# --- Stats
def test_stats():
    r = requests.get(f"{API}/sii/stats")
    assert r.status_code == 200
    d = r.json()
    for k in ["total", "correctas", "aceptadas_con_errores", "anuladas", "no_registradas", "ultimas"]:
        assert k in d
    assert isinstance(d["ultimas"], list)
    assert len(d["ultimas"]) <= 5


# --- CSV template & export
def test_csv_template():
    r = requests.get(f"{API}/sii/csv-template")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
    text = r.content.decode("utf-8-sig")
    assert "nif_titular" in text and "fecha_expedicion" in text


def test_batch_export_and_404():
    files = {"file": ("data.csv", CSV_COMMA.encode(), "text/csv")}
    d = requests.post(f"{API}/sii/consulta-batch", files=files).json()
    bid = d["batch_id"]
    r = requests.get(f"{API}/sii/batch/{bid}/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
    text = r.content.decode("utf-8-sig")
    assert "estado_factura" in text

    # filter listing by batch_id
    r2 = requests.get(f"{API}/sii/consultas", params={"batch_id": bid})
    assert r2.status_code == 200
    assert all(it.get("batch_id") == bid for it in r2.json())

    # 404 export
    r3 = requests.get(f"{API}/sii/batch/non-existent-batch/export")
    assert r3.status_code == 404
