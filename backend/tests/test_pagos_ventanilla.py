"""Tests for Pagos Ventanilla module."""
import os
import io
import re
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://soap-factura-batch.preview.emergentagent.com").rstrip("/")

ADMIN_EMAIL = "miguelingv@gmail.com"
ADMIN_PASSWORD = "MiguelAdmin2026!"

CSV_VALID = (
    "sociedad;nombre_cliente;cif_nif;direccion_social;direccion_suministro;cuenta_contrato;numero_factura;fecha_emision_factura;fecha_emision_doc;fecha_limite_pago;importe;validez_meses;sufijo;idioma\n"
    "TTE;Juan Pérez García;12345678Z;Calle Mayor 5 33012 Oviedo;CL Sagrado Corazón 12 33208 Gijón;CC0001234;2026A0000123;13.03.2026;20.03.2026;20.05.2026;109,95;5;510;es\n"
    "BASER;Empresa Cliente S.L.;B12345678;Av. Costa 25 33203 Gijón;Pol. Asipo Nave 12;CC0007890;2026B0000456;05.02.2026;20.02.2026;20.04.2026;1500,00;5;510;es\n"
    "BASER;María López;87654321X;Calle Pelayo 8;Calle Uría 30 Oviedo;CC0009876;2026B0000789;01.02.2026;15.02.2026;15.04.2026;50,00;5;510;es\n"
)

CSV_INVALID_SOC = (
    "sociedad;nombre_cliente;cif_nif;direccion_social;direccion_suministro;cuenta_contrato;numero_factura;fecha_emision_factura;fecha_emision_doc;fecha_limite_pago;importe;validez_meses;sufijo;idioma\n"
    "XX;Juan Test;12345678Z;Dir A;Dir B;CC1;F1;01.01.2026;02.01.2026;10.01.2026;10,00;5;510;es\n"
)

CSV_NO_IMPORTE = (
    "sociedad;nombre_cliente;cif_nif;direccion_social;direccion_suministro;cuenta_contrato;numero_factura;fecha_emision_factura;fecha_emision_doc;fecha_limite_pago;importe;validez_meses;sufijo;idioma\n"
    "TTE;Juan Test;12345678Z;Dir A;Dir B;CC1;F1;01.01.2026;02.01.2026;10.01.2026;0;5;510;es\n"
)


def _pdf_text(pdf_bytes):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((p.extract_text() or "") for p in r.pages)


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


# ---------------------------------------------------------- CSV template
def test_csv_template_download(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/csv-template")
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
    body = r.text
    assert "sociedad" in body and "numero_factura" in body
    # 1 header + at least 2 example rows
    non_empty = [ln for ln in body.splitlines() if ln.strip()]
    assert len(non_empty) >= 3


# ---------------------------------------------------------- Upload
def test_upload_valid_csv(admin_session):
    files = {"file": ("test.csv", CSV_VALID.encode("utf-8"), "text/csv")}
    r = admin_session.post(f"{BASE_URL}/api/pagos-ventanilla/upload", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["row_count"] == 3
    socs = {s["sociedad"]: s for s in data["by_sociedad"]}
    assert "TTE" in socs and "BASER" in socs
    assert socs["TTE"]["rows"] == 1
    assert socs["BASER"]["rows"] == 2
    assert abs(socs["BASER"]["importe_total"] - 1550.00) < 0.01
    # Preview present
    assert len(data["preview"]) == 3
    return data["id"]


def test_upload_invalid_sociedad(admin_session):
    files = {"file": ("bad.csv", CSV_INVALID_SOC.encode("utf-8"), "text/csv")}
    r = admin_session.post(f"{BASE_URL}/api/pagos-ventanilla/upload", files=files)
    assert r.status_code == 400
    assert "Sociedad desconocida" in r.text or "inválido" in r.text.lower()


def test_upload_zero_importe(admin_session):
    files = {"file": ("zero.csv", CSV_NO_IMPORTE.encode("utf-8"), "text/csv")}
    r = admin_session.post(f"{BASE_URL}/api/pagos-ventanilla/upload", files=files)
    assert r.status_code == 400


# ---------------------------------------------------------- Generate
@pytest.fixture(scope="module")
def generated_job(admin_session):
    files = {"file": ("gen.csv", CSV_VALID.encode("utf-8"), "text/csv")}
    r = admin_session.post(f"{BASE_URL}/api/pagos-ventanilla/upload", files=files)
    assert r.status_code == 200
    upload_id = r.json()["id"]
    r2 = admin_session.post(f"{BASE_URL}/api/pagos-ventanilla/generate",
                            json={"upload_id": upload_id})
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["generated_count"] == 3
    assert data["error_count"] == 0
    assert data["status"] == "completado"
    assert len(data["files"]) == 3
    return data


def test_generated_files_have_pdf_ext(generated_job):
    for f in generated_job["files"]:
        assert f.endswith(".pdf")


# ---------------------------------------------------------- Search filters
def test_search_no_filter(admin_session, generated_job):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 3
    assert "items" in data


def test_search_filter_sociedad_TTE(admin_session, generated_job):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search?sociedad=TTE")
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(it["sociedad"] == "TTE" for it in items)


def test_search_filter_sociedad_BASER(admin_session, generated_job):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search?sociedad=BASER")
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(it["sociedad"] == "BASER" for it in items)


def test_search_filter_importe_min(admin_session, generated_job):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search?importe_min=1000")
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert it["importe"] >= 1000


def test_search_filter_importe_max(admin_session, generated_job):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search?importe_max=100")
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert it["importe"] <= 100


def test_search_filter_cif(admin_session, generated_job):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search?cif_nif=12345678Z")
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert "12345678Z" in it["cif_nif"].upper()


def test_search_filter_numero_factura(admin_session, generated_job):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search?numero_factura=2026A0000123")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    assert all("2026A0000123" in it["numero_factura"] for it in items)


def test_search_filter_estado(admin_session, generated_job):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search?estado=OK")
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert it["estado"] == "OK"


# ---------------------------------------------------------- Download token + PDF download
def test_download_token(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/jobs/auth/download-token")
    assert r.status_code == 200
    assert "token" in r.json()


def test_download_pdf_via_token(admin_session, generated_job):
    job_id = generated_job["id"]
    fname = generated_job["files"][0]
    tok = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/jobs/auth/download-token").json()["token"]
    r = requests.get(
        f"{BASE_URL}/api/pagos-ventanilla/jobs/{job_id}/files/{fname}?token={tok}"
    )
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


# ---------------------------------------------------------- CORREOS rule (Baser >=999.99 → no CORREOS)
def test_baser_high_amount_excludes_correos(admin_session, generated_job):
    """Find the BASER 1500€ PDF and check its text does NOT contain CORREOS."""
    job_id = generated_job["id"]
    tok = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/jobs/auth/download-token").json()["token"]
    # find file with 2026B0000456
    target = next((f for f in generated_job["files"] if "2026B0000456" in f), None)
    assert target, f"No file found for BASER 1500€ invoice: {generated_job['files']}"
    r = requests.get(
        f"{BASE_URL}/api/pagos-ventanilla/jobs/{job_id}/files/{target}?token={tok}"
    )
    assert r.status_code == 200
    # extract text via pdftotext
    txt = _pdf_text(r.content)
    assert "CORREOS" not in txt.upper(), f"BASER 1500€ PDF should NOT include CORREOS. Text snippet: {txt[:500]}"


def test_baser_low_amount_includes_correos(admin_session, generated_job):
    """Find the BASER 50€ PDF and check CORREOS is present."""
    job_id = generated_job["id"]
    tok = admin_session.get(f"{BASE_URL}/api/pagos-ventanilla/jobs/auth/download-token").json()["token"]
    target = next((f for f in generated_job["files"] if "2026B0000789" in f), None)
    assert target, f"No file found for BASER 50€ invoice: {generated_job['files']}"
    r = requests.get(
        f"{BASE_URL}/api/pagos-ventanilla/jobs/{job_id}/files/{target}?token={tok}"
    )
    assert r.status_code == 200
    txt = _pdf_text(r.content)
    assert "CORREOS" in txt.upper(), f"BASER 50€ PDF MUST include CORREOS. Text snippet: {txt[:500]}"


# ---------------------------------------------------------- RBAC (usuario role)
def test_usuario_role_has_pagos_ventanilla_perms(admin_session):
    r = admin_session.get(f"{BASE_URL}/api/admin/roles")
    assert r.status_code == 200
    roles = r.json()
    usuario = next((x for x in roles if x.get("name") == "usuario"), None)
    assert usuario, "Role 'usuario' not found"
    perms = usuario.get("permissions", [])
    assert "pagos_ventanilla.view" in perms, f"usuario perms: {perms}"
    assert "pagos_ventanilla.manage" in perms, f"usuario perms: {perms}"


# ---------------------------------------------------------- Auth required
def test_search_requires_auth():
    r = requests.get(f"{BASE_URL}/api/pagos-ventanilla/pagos/search")
    assert r.status_code in (401, 403)
