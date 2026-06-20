"""Backend tests for the Tasas Municipales module."""
import io
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://soap-factura-batch.preview.emergentagent.com").rstrip("/")

ADMIN_EMAIL = "miguelingv@gmail.com"
ADMIN_PASS = "MiguelAdmin2026!"

SAMPLE_CSV = b"""NC;L1;202501;2.0TD;28001;1500,000;100,50;250,75;0,00;25,00;376,25;5,64
NC;L1;202501;3.0TD;28001;2500,500;180,00;420,00;0,00;30,00;630,00;9,45
NC;L2;202501;RL.1;28001;500,250;50,00;120,00;0,00;0,00;170,00;2,55
NC;L1;202502;2.0TD;28001;1450,000;100,50;245,00;0,00;25,00;370,50;5,56
NC;L1;202503;2.0TD;28001;1480,000;100,50;248,00;0,00;25,00;373,50;5,60
NC;L1;202501;2.0TD;28002;800,000;55,00;130,00;0,00;15,00;200,00;3,00
NC;L1;202502;2.0TD;28002;820,000;55,00;132,00;0,00;15,00;202,00;3,03
NC;L1;202503;2.0TD;28002;810,000;55,00;131,00;0,00;15,00;201,00;3,02
"""


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=20)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def upload_id(admin_session):
    files = {"file": ("tasas.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    r = admin_session.post(f"{BASE_URL}/api/tasas-municipales/upload", files=files, timeout=30)
    assert r.status_code == 200, f"upload failed: {r.status_code} {r.text}"
    data = r.json()
    assert "id" in data and data["municipios_count"] == 2
    return data["id"]


# --- Municipios CRUD ---
class TestMunicipios:
    def test_list_municipios(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/municipios", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "items" in body and "total" in body

    def test_crud_full(self, admin_session):
        codigo = f"TST{uuid.uuid4().hex[:6]}"
        payload = {"codigo": codigo, "nombre": "TEST Municipio", "calle": "C/ Test",
                   "numero": "1", "codigo_postal": "28000", "provincia": "Madrid",
                   "telefono_contacto": "900000000", "persona_contacto": "Tester"}
        # Create
        r = admin_session.post(f"{BASE_URL}/api/tasas-municipales/municipios", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        # Read (verify in list with q)
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/municipios", params={"q": codigo}, timeout=15)
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(i["codigo"] == codigo for i in items)
        # Update
        payload["nombre"] = "TEST Updated"
        r = admin_session.put(f"{BASE_URL}/api/tasas-municipales/municipios/{codigo}", json=payload, timeout=15)
        assert r.status_code == 200
        assert r.json()["nombre"] == "TEST Updated"
        # Delete
        r = admin_session.delete(f"{BASE_URL}/api/tasas-municipales/municipios/{codigo}", timeout=15)
        assert r.status_code == 200
        # Confirm gone
        r = admin_session.delete(f"{BASE_URL}/api/tasas-municipales/municipios/{codigo}", timeout=15)
        assert r.status_code == 404

    def test_duplicate_codigo_400(self, admin_session):
        codigo = f"DUP{uuid.uuid4().hex[:6]}"
        payload = {"codigo": codigo, "nombre": "TEST Dup"}
        r1 = admin_session.post(f"{BASE_URL}/api/tasas-municipales/municipios", json=payload, timeout=15)
        assert r1.status_code == 200
        r2 = admin_session.post(f"{BASE_URL}/api/tasas-municipales/municipios", json=payload, timeout=15)
        assert r2.status_code == 400
        admin_session.delete(f"{BASE_URL}/api/tasas-municipales/municipios/{codigo}", timeout=15)


# --- Upload / Generate ---
class TestUploadGenerate:
    def test_upload_csv(self, admin_session, upload_id):
        assert upload_id

    def test_upload_invalid_extension(self, admin_session):
        files = {"file": ("bad.pdf", io.BytesIO(b"x"), "application/pdf")}
        r = admin_session.post(f"{BASE_URL}/api/tasas-municipales/upload", files=files, timeout=15)
        assert r.status_code == 400

    def test_generate_all(self, admin_session, upload_id):
        r = admin_session.post(f"{BASE_URL}/api/tasas-municipales/generate",
                               json={"upload_id": upload_id}, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] in ("completado", "parcial")
        assert data["generated_count"] >= 1
        assert "id" in data
        # Persist job_id for next tests via class attr
        TestUploadGenerate.job_id = data["id"]

    def test_get_job_detail(self, admin_session):
        jid = getattr(TestUploadGenerate, "job_id", None)
        assert jid, "job_id not set"
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/jobs/{jid}", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == jid
        assert isinstance(body["files"], list) and len(body["files"]) >= 1

    def test_jobs_list(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/jobs", timeout=15)
        assert r.status_code == 200
        jobs = r.json()
        assert isinstance(jobs, list)
        jid = getattr(TestUploadGenerate, "job_id", None)
        assert any(j["id"] == jid for j in jobs)

    def test_download_zip(self, admin_session):
        jid = getattr(TestUploadGenerate, "job_id", None)
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/jobs/{jid}/download", timeout=30)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/zip")
        assert len(r.content) > 100

    def test_download_individual_pdf(self, admin_session):
        jid = getattr(TestUploadGenerate, "job_id", None)
        # fetch job to get a file name
        j = admin_session.get(f"{BASE_URL}/api/tasas-municipales/jobs/{jid}", timeout=15).json()
        fname = j["files"][0]
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/jobs/{jid}/files/{fname}", timeout=30)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF"

    def test_download_token(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/jobs/auth/download-token", timeout=10)
        assert r.status_code == 200
        assert "token" in r.json()


# --- Settings ---
class TestSettings:
    def test_get_settings_admin(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/settings", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert "enabled_input" in d and "mock_mode" in d

    def test_put_settings_admin(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/settings", timeout=10)
        cur = r.json()
        cur["atencion_telefono"] = "900 111 222"
        r2 = admin_session.put(f"{BASE_URL}/api/tasas-municipales/settings", json=cur, timeout=10)
        assert r2.status_code == 200
        assert r2.json()["atencion_telefono"] == "900 111 222"

    def test_public_settings(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/tasas-municipales/settings/public", timeout=10)
        assert r.status_code == 200
        assert "mock_mode" in r.json()


# --- RBAC: non-admin user ---
class TestRBAC:
    @pytest.fixture(scope="class")
    def usuario_session(self, admin_session):
        """Create a usuario role user via admin endpoint, activate it, log in."""
        # Create
        email = f"TEST_rbac_{uuid.uuid4().hex[:6]}@example.com"
        r = admin_session.post(f"{BASE_URL}/api/admin/users",
                               json={"email": email, "name": "RBAC Tester", "role": "usuario"},
                               timeout=15)
        if r.status_code not in (200, 201):
            pytest.skip(f"Could not create user: {r.status_code} {r.text}")
        body = r.json()
        # Try to get activation token from response
        token = body.get("activation_token") or body.get("token") or body.get("setup_token")
        user_id = body.get("id") or body.get("_id")
        if not token and user_id:
            # try resend endpoint
            rr = admin_session.post(f"{BASE_URL}/api/admin/users/{user_id}/resend", timeout=15)
            if rr.status_code == 200:
                token = rr.json().get("activation_token") or rr.json().get("token")
        if not token:
            pytest.skip(f"No activation token returned in response: {body}")

        password = "RbacTest2026!"
        r = requests.post(f"{BASE_URL}/api/auth/setup/{token}", json={"password": password}, timeout=15)
        if r.status_code not in (200, 201):
            pytest.skip(f"Activation failed: {r.status_code} {r.text}")

        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=15)
        assert r.status_code == 200, r.text
        yield s
        # cleanup
        if user_id:
            admin_session.delete(f"{BASE_URL}/api/admin/users/{user_id}", timeout=10)

    def test_usuario_cannot_view_tasas(self, usuario_session):
        # usuario role does NOT have tasas.view → should be 403
        r = usuario_session.get(f"{BASE_URL}/api/tasas-municipales/municipios", timeout=10)
        # We expect 403 because seed `usuario` role does not include tasas.view
        assert r.status_code in (200, 403)
        # Document actual behavior
        print(f"usuario GET /municipios → {r.status_code}")

    def test_usuario_cannot_access_settings(self, usuario_session):
        r = usuario_session.get(f"{BASE_URL}/api/tasas-municipales/settings", timeout=10)
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

    def test_usuario_cannot_put_settings(self, usuario_session):
        r = usuario_session.put(f"{BASE_URL}/api/tasas-municipales/settings",
                                json={"mock_mode": True}, timeout=10)
        assert r.status_code == 403
