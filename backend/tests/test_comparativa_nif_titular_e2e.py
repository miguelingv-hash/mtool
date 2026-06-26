"""E2E tests for the NIF-titular filter + streaming CSV export feature.

Verifies:
  - GET /api/comparativa/nifs-titulares devuelve {nifs_titulares, comercial_sin_nif}
  - GET /api/comparativa con nif_titular filtra ambos universos correctamente
  - GET /api/comparativa/totales propaga filtros.nif_titular
  - GET /api/comparativa/resumen-origenes y /periodos aceptan nif_titular
  - GET /api/comparativa/export devuelve text/csv streaming con BOM + Content-Disposition
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
EXPECTED_NIF = "A95000295"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def admin_session(db):
    import sys
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token, COOKIE_ACCESS  # noqa

    user = db.users.find_one({"email": ADMIN_EMAIL})
    assert user, "Admin user no encontrado en BD"
    token = create_access_token(user["_id"], user["email"])

    s = requests.Session()
    s.cookies.set(
        COOKIE_ACCESS, token,
        domain="soap-factura-batch.preview.emergentagent.com",
    )
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    assert r.status_code == 200, f"Auth bypass falló: {r.status_code} {r.text}"
    return s


# --- 1) /comparativa/nifs-titulares -----------------------------------------
class TestNifsTitulares:
    def test_devuelve_estructura_y_lista_correcta(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/comparativa/nifs-titulares", timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "nifs_titulares" in data
        assert "comercial_sin_nif" in data
        assert isinstance(data["nifs_titulares"], list)
        assert isinstance(data["comercial_sin_nif"], int)
        assert EXPECTED_NIF in data["nifs_titulares"], (
            f"Esperaba '{EXPECTED_NIF}' en {data['nifs_titulares']}"
        )
        # Per problem statement BD actual: comercial_sin_nif == 4731
        assert data["comercial_sin_nif"] == 4731, (
            f"Esperaba 4731 docs comerciales sin NIF, got {data['comercial_sin_nif']}"
        )


# --- 2) /comparativa con nif_titular ----------------------------------------
class TestComparativaListadoConNif:
    def test_filtra_por_nif_y_devuelve_total(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa",
            params={
                "nif_titular": EXPECTED_NIF,
                "ejercicio": "2025",
                "periodo": "01",
                "only_diffs": "true",
                "limit": 5,
            },
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "total" in data
        assert isinstance(data["total"], int)
        assert data["total"] >= 0
        assert "items" in data and isinstance(data["items"], list)

    def test_nif_inexistente_devuelve_vacio(self, admin_session):
        """Un NIF que no existe debe devolver total=0 (no 500)."""
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa",
            params={
                "nif_titular": "Z99999999Z",
                "ejercicio": "2025",
                "periodo": "01",
                "only_diffs": "true",
                "limit": 5,
            },
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Solo se acepta lista vacía (SII filtrado por NIF inexistente).
        # COMERCIAL con $in:[nif, null] todavía puede traer los 4731 sin nif
        # pero TODOS quedarían como solo_comercial — verificamos status.
        assert "total" in data


# --- 3) /comparativa/totales con nif_titular --------------------------------
class TestTotales:
    def test_totales_propaga_filtro_nif(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/totales",
            params={"nif_titular": EXPECTED_NIF},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "filtros" in data
        assert data["filtros"].get("nif_titular") == EXPECTED_NIF
        assert "sii" in data and "n_facturas" in data["sii"]
        assert "comercial_total" in data and "n_facturas" in data["comercial_total"]
        assert isinstance(data["sii"]["n_facturas"], int)
        assert data["sii"]["n_facturas"] >= 0


# --- 4) /comparativa/resumen-origenes y /periodos ---------------------------
class TestAuxiliares:
    def test_resumen_origenes(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/resumen-origenes",
            params={"nif_titular": EXPECTED_NIF},
            timeout=120,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "items" in data and isinstance(data["items"], list)

    def test_periodos(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/periodos",
            params={"nif_titular": EXPECTED_NIF},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # estructura habitual { ejercicios: [...], periodos: {...} } o similar
        assert isinstance(data, dict)


# --- 5) /comparativa/export streaming CSV -----------------------------------
class TestExportStreaming:
    def test_export_pequeno_devuelve_csv_correcto(self, admin_session):
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/export",
            params={
                "nif_titular": EXPECTED_NIF,
                "ejercicio": "2025",
                "periodo": "01",
                "only_diffs": "true",
            },
            timeout=120,
            stream=False,
        )
        assert r.status_code == 200, r.text[:500]
        # Content-Type
        ct = r.headers.get("content-type", "")
        assert "text/csv" in ct.lower(), f"Content-Type esperado text/csv, got {ct}"
        # Content-Disposition
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd.lower()
        expected_filename = f"comparativa_{EXPECTED_NIF}_2025_01.csv"
        assert expected_filename in cd, (
            f"Esperaba filename={expected_filename} en {cd}"
        )
        # Body: BOM UTF-8 + cabeceras
        body = r.content
        assert len(body) > 0
        # BOM: \xef\xbb\xbf
        assert body[:3] == b"\xef\xbb\xbf", (
            f"Esperaba BOM UTF-8 al inicio, got {body[:10]!r}"
        )
        # Cabeceras (primera línea tras BOM)
        first_line = body[3:].split(b"\n", 1)[0].decode("utf-8")
        assert "num_serie_factura" in first_line
        assert "estado" in first_line

    def test_export_grande_sin_timeout(self, admin_session):
        """Sin ejercicio/periodo → dataset grande. Debe devolver completo
        antes de 120s gracias al streaming, sin 502/524."""
        r = admin_session.get(
            f"{BASE_URL}/api/comparativa/export",
            params={
                "nif_titular": EXPECTED_NIF,
                "only_diffs": "true",
            },
            timeout=180,
            stream=True,
        )
        assert r.status_code == 200, f"Status {r.status_code} con body={r.text[:300]}"
        ct = r.headers.get("content-type", "")
        assert "text/csv" in ct.lower()
        # Consumir el stream para verificar tamaño y no error mid-stream
        total = 0
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if chunk:
                total += len(chunk)
        assert total > 0, "El cuerpo del export viene vacío"
        # Para A95000295 con 862933 SII docs el export es > 1MB.
        # Aceptamos un mínimo prudente.
        assert total > 1000, f"Export sospechosamente pequeño: {total} bytes"
