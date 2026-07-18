"""Iter24: Cuadro de conciliación mensual (/api/comparativa/cuadro-mensual)

Validaciones:
  1. El endpoint requiere `nif_titular` y `ejercicio` (400 si faltan).
  2. Devuelve la lista `origenes` (SIGLO / SAP) y `rows` estructurados.
  3. Cada row tiene los bloques `sii`, `comercial_por_origen`,
     `delta_por_origen`, `pct_conciliacion_por_origen`.
  4. Los deltas coinciden con SII − Comercial (por origen).
  5. Los totales del cuadro coinciden con `/comparativa/totales`
     (mismos filtros) para la sociedad + ejercicio.
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


def test_400_sin_parametros_obligatorios():
    r = requests.get(f"{BASE_URL}/comparativa/cuadro-mensual", headers=HDR, timeout=30)
    # Sin nif_titular y sin ejercicio → FastAPI devuelve 422 (query missing).
    assert r.status_code in (400, 422), f"{r.status_code}: {r.text[:300]}"


def test_cuadro_mensual_baser_2026():
    """Comprobar estructura completa contra la sociedad BASER."""
    r = requests.get(
        f"{BASE_URL}/comparativa/cuadro-mensual",
        params={"nif_titular": "A74251836", "ejercicio": "2026"},
        headers=HDR,
        timeout=180,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    d = r.json()

    # Estructura
    assert "filtros" in d
    assert "origenes" in d
    assert "rows" in d and isinstance(d["rows"], list)
    assert "totales" in d

    assert d["filtros"]["nif_titular"] == "A74251836"
    assert d["filtros"]["ejercicio"] == "2026"

    # Debe haber al menos 1 origen y 1 fila
    assert len(d["origenes"]) >= 1
    assert len(d["rows"]) >= 1

    row = d["rows"][0]
    assert "periodo" in row
    assert "tipo_factura" in row
    assert "sii" in row and all(k in row["sii"] for k in ("base", "cuota", "n"))
    assert "comercial_por_origen" in row
    assert "delta_por_origen" in row
    assert "pct_conciliacion_por_origen" in row

    # Cada origen debe tener los tres bloques
    for og in d["origenes"]:
        assert og in row["comercial_por_origen"]
        assert og in row["delta_por_origen"]
        assert og in row["pct_conciliacion_por_origen"]

    # Delta coherente: sii - comercial (por base)
    for row in d["rows"]:
        for og in d["origenes"]:
            sii_base = row["sii"]["base"]
            com_base = row["comercial_por_origen"][og]["base"]
            d_base = row["delta_por_origen"][og]["base"]
            assert abs(round(sii_base - com_base, 2) - d_base) < 0.01, (
                f"Delta mal calculado para {row['periodo']}/{row['tipo_factura']}/{og}: "
                f"sii={sii_base} com={com_base} delta={d_base}"
            )


def test_totales_coinciden_con_endpoint_totales():
    """Los totales del cuadro deben cuadrar con /comparativa/totales
    para el mismo scope (sociedad+ejercicio)."""
    r_cuadro = requests.get(
        f"{BASE_URL}/comparativa/cuadro-mensual",
        params={"nif_titular": "A74251836", "ejercicio": "2026"},
        headers=HDR,
        timeout=180,
    )
    assert r_cuadro.status_code == 200
    cuadro = r_cuadro.json()

    r_tot = requests.get(
        f"{BASE_URL}/comparativa/totales",
        params={"nif_titular": "A74251836", "ejercicio": "2026"},
        headers=HDR,
        timeout=180,
    )
    assert r_tot.status_code == 200
    tot = r_tot.json()

    # SII base y cuota deben coincidir (misma agregación de fondo).
    assert abs(cuadro["totales"]["sii"]["base"] - tot["sii"]["base"]) < 1, (
        f"SII base disparejo: cuadro={cuadro['totales']['sii']['base']} "
        f"totales={tot['sii']['base']}"
    )
    assert cuadro["totales"]["sii"]["n"] == tot["sii"]["n_facturas"]

    # Cada origen: coincidencia en base y n_facturas.
    for og in cuadro["origenes"]:
        c_og = cuadro["totales"]["comercial_por_origen"].get(og)
        t_og = tot["comercial_por_origen"].get(og)
        assert c_og and t_og, f"Origen {og} no aparece en ambos endpoints"
        assert abs(c_og["base"] - t_og["base"]) < 1, (
            f"{og} base disparejo: cuadro={c_og['base']} totales={t_og['base']}"
        )
        assert c_og["n"] == t_og["n_facturas"], (
            f"{og} n_facturas disparejo: cuadro={c_og['n']} totales={t_og['n_facturas']}"
        )


def test_filtro_por_periodo():
    """Si pasamos periodo=06, el cuadro sólo debe contener rows con periodo='06'."""
    r = requests.get(
        f"{BASE_URL}/comparativa/cuadro-mensual",
        params={
            "nif_titular": "A74251836",
            "ejercicio": "2026",
            "periodo": "06",
        },
        headers=HDR,
        timeout=180,
    )
    assert r.status_code == 200
    d = r.json()
    for row in d["rows"]:
        assert row["periodo"] == "06", (
            f"Fila con periodo distinto: {row['periodo']}"
        )
