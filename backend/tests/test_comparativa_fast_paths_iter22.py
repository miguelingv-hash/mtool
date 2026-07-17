"""Tests iter22: fast-paths de solo_comercial y solo_sii con sort_by/num_serie.

Bugs previos que este test protege:
  1. `estado=solo_comercial + sort_by` caía al legacy path y hacía `to_list(None)`
     sobre 1,5M docs → OOM del pod. Ahora usa aggregation con $lookup.
  2. `$arrayElemAt` de un array vacío devuelve `undefined` (no null) → el
     patrón `$ne [$_sii_raw, None]` NO detectaba ausencia. Migramos todos
     los usos a `$size(_sii_docs) > 0` que sí es fiable.
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
    """Mint JWT directo (esquiva MFA para tests)."""
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")
    from auth import create_access_token
    return create_access_token("admin-seed", "miguelingv@gmail.com")


HDR = {"Authorization": f"Bearer {_jwt()}"}


def test_solo_comercial_sin_sort_ni_num_serie():
    """Path base — el que sí funcionaba antes."""
    r = requests.get(
        f"{BASE_URL}/comparativa/bundle",
        params={"estado": "solo_comercial", "limit": 5, "nif_titular": "A74251836"},
        headers=HDR,
        timeout=180,
    )
    assert r.status_code == 200
    data = r.json()["list"]
    # Todos los items deben ser solo_comercial (ni discrepancia ni coincide).
    for it in data["items"]:
        assert it["estado"] == "solo_comercial", (
            f"Item {it.get('num_serie_factura')} tiene estado {it.get('estado')}"
        )
        assert it.get("sii") is None, (
            "solo_comercial nunca debe traer contraparte SII"
        )


def test_solo_comercial_con_sort_no_ooma():
    """Antes: `sort_by` saltaba al legacy path → OOM."""
    r = requests.get(
        f"{BASE_URL}/comparativa/bundle",
        params={
            "estado": "solo_comercial",
            "limit": 5,
            "nif_titular": "A74251836",
            "sort_by": "fecha_expedicion",
            "sort_dir": "desc",
        },
        headers=HDR,
        timeout=180,
    )
    assert r.status_code == 200, (
        f"Bundle solo_comercial+sort devolvió {r.status_code}: {r.text[:300]}"
    )
    data = r.json()["list"]
    # No hay estados mezclados
    estados_unicos = {it["estado"] for it in data["items"]}
    assert estados_unicos.issubset({"solo_comercial"}), (
        f"Se colaron otros estados en el filtro: {estados_unicos}"
    )


def test_solo_sii_con_sort():
    """Simétrico: solo_sii con sort también debe usar fast-path Mongo."""
    r = requests.get(
        f"{BASE_URL}/comparativa/bundle",
        params={
            "estado": "solo_sii",
            "limit": 5,
            "nif_titular": "A74251836",
            "sort_by": "num_serie_factura",
            "sort_dir": "asc",
        },
        headers=HDR,
        timeout=180,
    )
    assert r.status_code == 200
    data = r.json()["list"]
    for it in data["items"]:
        assert it["estado"] == "solo_sii", (
            f"Item {it.get('num_serie_factura')} estado incorrecto: {it['estado']}"
        )
        assert it.get("comercial") is None, (
            "solo_sii nunca debe traer contraparte comercial"
        )


def test_solo_sii_sin_nif_no_ooma():
    """Bug reportado: `estado=solo_sii` sin NIF con 1,5M docs pasaba por
    legacy path (com_docs.to_list(None)) → OOM → estados mezclados en
    el listado. Ahora usa fast-path aggregation directo."""
    r = requests.get(
        f"{BASE_URL}/comparativa/bundle",
        params={"estado": "solo_sii", "limit": 15},
        headers=HDR,
        timeout=200,
    )
    assert r.status_code == 200, (
        f"solo_sii sin NIF devolvió {r.status_code}: {r.text[:300]}"
    )
    data = r.json()["list"]
    estados_unicos = {it["estado"] for it in data["items"]}
    assert estados_unicos.issubset({"solo_sii"}), (
        f"Filtro solo_sii sin NIF devolvió otros estados: {estados_unicos}"
    )
    for it in data["items"]:
        assert it.get("comercial") is None, (
            f"Item {it.get('num_serie_factura')} en solo_sii trae comercial"
        )


def test_estado_no_mezcla_con_ningun_filtro():
    """Regresión: al pedir un estado concreto NUNCA debe aparecer otro."""
    for estado in ["coincide", "discrepancia", "solo_comercial", "solo_sii"]:
        r = requests.get(
            f"{BASE_URL}/comparativa/bundle",
            params={"estado": estado, "limit": 10, "nif_titular": "A74251836"},
            headers=HDR,
            timeout=180,
        )
        assert r.status_code == 200, f"{estado}: {r.status_code}"
        items = r.json()["list"]["items"]
        estados_reales = {it["estado"] for it in items}
        assert estados_reales.issubset({estado}), (
            f"Filtro {estado} devolvió items con estados: {estados_reales}"
        )


def test_export_devuelve_filas_ademas_de_cabecera():
    """Bug: export CSV con dataset >100k docs devolvía sólo la cabecera
    porque `com_docs.to_list(length=None)` OOM-killaba el generator.
    Ahora usa aggregation streaming (cursor + $lookup) → memoria constante."""
    r = requests.get(
        f"{BASE_URL}/comparativa/export",
        params={
            "nif_titular": "A74251836",
            "estado": "solo_comercial",
        },
        headers=HDR,
        timeout=180,
    )
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    body = r.text
    lines = [ln for ln in body.splitlines() if ln.strip()]
    # Al menos la cabecera + 1 fila de datos.
    assert len(lines) >= 2, (
        f"Export devolvió sólo {len(lines)} líneas — el bug OOM podría "
        "haber vuelto: cabecera sin datos."
    )
    # La cabecera empieza con num_serie_factura (con BOM)
    assert "num_serie_factura" in lines[0]
    # Las filas siguientes deben tener num_serie_factura + estado=solo_comercial
    for ln in lines[1:6]:
        assert "solo_comercial" in ln, (
            f"Fila no tiene el estado esperado: {ln[:120]}"
        )


def test_has_sii_usa_size_no_ne_null():
    """El patrón `$ne [$_sii_raw, None]` es bugueado con MongoDB porque
    `$arrayElemAt` de array vacío devuelve `undefined`, no `null`. Este
    test comprueba que el código no usa ese patrón bugueado en los
    fast-paths aggregation."""
    from pathlib import Path
    src = Path("/app/backend/router_facturas.py").read_text()
    # Whitelist: los únicos usos de `$ne [$_sii_raw, None]` aceptables serían
    # cuando `_sii_raw` se ha rehidratado desde una fuente distinta a
    # `$arrayElemAt`. En este proyecto no debería haber ninguno.
    ocurrencias = src.count('"$ne": ["$_sii_raw", None]')
    assert ocurrencias == 0, (
        f"Se detectaron {ocurrencias} usos del patrón bugueado "
        f'`$ne [$_sii_raw, None]`. Usa `$size(_sii_docs) > 0` en su lugar.'
    )
