"""Regression test iter31: rectificativas por Sustitución.

AEAT devuelve la base y cuota reales bajo <ImporteRectificacion> cuando
`TipoFactura ∈ R1..R5` y `TipoRectificativa=S` (Sustitución). El
<DesgloseIVA> viene a 0.

Verifica:
1) `_canonical_amount` devuelve base_rect + cuota_rect en R Sustitución.
2) `_es_rectificativa_sustitucion` distingue S vs I.
3) `diff_facturas` promociona los rectificados a top-level → comparación
   correcta contra el comercial.
4) Parser CSV Newman lee las 3 columnas nuevas.
"""
import io
import pytest

import sys
sys.path.insert(0, "/app/backend")

from factura_model import (  # noqa: E402
    _canonical_amount,
    _es_rectificativa_sustitucion,
    _base_efectiva,
    _cuota_efectiva,
    diff_facturas,
)


# ---------------------------------------------------------------------------
# 1. _canonical_amount con rectificativas
# ---------------------------------------------------------------------------
def test_canonical_r_sustitucion_usa_rectificados():
    """R Sustitución con desglose=0 pero base_rect+cuota_rect != 0."""
    doc = {
        "num_serie_factura": "2TSS260600000007",
        "tipo_factura": "R5",
        "tipo_rectificativa": "S",
        "base_imponible": 0.0,
        "cuota_repercutida": 0.0,
        "importe_total": 0.0,
        "base_rectificada": 80.33,
        "cuota_rectificada": 16.87,
    }
    can = _canonical_amount(doc)
    assert can == pytest.approx(97.20, abs=0.01), (
        f"Canonical R Sustitución debería ser 80.33+16.87=97.20, "
        f"obtenido {can}"
    )


def test_canonical_r_diferencia_usa_desglose():
    """R por Diferencia (TipoRectificativa=I): base/cuota vienen en el
    desglose normal, no debe usar los rectificados."""
    doc = {
        "num_serie_factura": "R-DIF-1",
        "tipo_factura": "R2",
        "tipo_rectificativa": "I",
        "base_imponible": 50.0,
        "cuota_repercutida": 10.50,
        "importe_total": 60.50,
    }
    can = _canonical_amount(doc)
    assert can == pytest.approx(60.50, abs=0.01)


def test_canonical_f1_no_afectado():
    """Facturas normales F1: se ignora cualquier campo rectificativo."""
    doc = {
        "num_serie_factura": "F1-NORMAL",
        "tipo_factura": "F1",
        "base_imponible": 100.0,
        "cuota_repercutida": 21.0,
        "importe_total": 121.0,
    }
    can = _canonical_amount(doc)
    assert can == pytest.approx(121.0, abs=0.01)


# ---------------------------------------------------------------------------
# 2. _es_rectificativa_sustitucion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tipo,rect,esperado", [
    ("R1", "S", True), ("R2", "S", True), ("R3", "S", True),
    ("R4", "S", True), ("R5", "S", True),
    ("R1", "I", False), ("R2", None, False), ("R5", "", False),
    ("F1", "S", False), ("F2", "S", False),
    ("r5", "s", True),  # case-insensitive
])
def test_es_rectificativa_sustitucion(tipo, rect, esperado):
    assert _es_rectificativa_sustitucion({
        "tipo_factura": tipo, "tipo_rectificativa": rect,
    }) is esperado


# ---------------------------------------------------------------------------
# 3. _base_efectiva / _cuota_efectiva
# ---------------------------------------------------------------------------
def test_base_cuota_efectiva_r_sustitucion():
    doc = {
        "tipo_factura": "R5", "tipo_rectificativa": "S",
        "base_imponible": 0.0, "cuota_repercutida": 0.0,
        "base_rectificada": 80.33, "cuota_rectificada": 16.87,
    }
    assert _base_efectiva(doc) == pytest.approx(80.33)
    assert _cuota_efectiva(doc) == pytest.approx(16.87)


def test_base_cuota_efectiva_no_rectificativa():
    doc = {
        "tipo_factura": "F1",
        "base_imponible": 100.0, "cuota_repercutida": 21.0,
        # Aunque haya campos rectificativos por error, no aplican.
        "base_rectificada": 999.0, "cuota_rectificada": 999.0,
    }
    assert _base_efectiva(doc) == pytest.approx(100.0)
    assert _cuota_efectiva(doc) == pytest.approx(21.0)


# ---------------------------------------------------------------------------
# 4. diff_facturas promociona rectificados
# ---------------------------------------------------------------------------
def test_diff_r_sustitucion_coincide_con_comercial():
    """SII trae R Sustitución con base=cuota=importe=0 pero rectificada
    80.33/16.87. Comercial guarda base=80.33/cuota=16.87 (importe
    rectificado real). Debe marcar coincide."""
    sii = {
        "num_serie_factura": "2TSS260600000007",
        "tipo_factura": "R5",
        "tipo_rectificativa": "S",
        "base_imponible": 0.0,
        "cuota_repercutida": 0.0,
        "importe_total": 0.0,
        "base_rectificada": 80.33,
        "cuota_rectificada": 16.87,
        "tipo_impositivo": 21.0,
        "fecha_expedicion": "09-06-2026",
        "ejercicio": "2026",
        "periodo": "06",
    }
    com = {
        "num_serie_factura": "2TSS260600000007",
        "tipo_factura": "R5",
        "base_imponible": 80.33,
        "cuota_repercutida": 16.87,
        "importe_total": 97.20,
        "tipo_impositivo": 21.0,
        "fecha_expedicion": "09-06-2026",
        "ejercicio": "2026",
        "periodo": "06",
    }
    diff = diff_facturas(sii, com)
    diff_reales = {k: v for k, v in diff.items() if not k.startswith("_")}
    assert not diff_reales, (
        f"R Sustitución debería reconciliar: SII rectificado ({sii['base_rectificada']}"
        f"/{sii['cuota_rectificada']}) == Comercial "
        f"({com['base_imponible']}/{com['cuota_repercutida']}). "
        f"Diffs reales: {diff_reales}"
    )


def test_diff_r_sustitucion_detecta_diferencia_real():
    """Si el comercial NO cuadra con el rectificado, sí debe reportar
    discrepancia."""
    sii = {
        "num_serie_factura": "R-BAD",
        "tipo_factura": "R5", "tipo_rectificativa": "S",
        "base_imponible": 0.0, "cuota_repercutida": 0.0,
        "importe_total": 0.0,
        "base_rectificada": 100.0, "cuota_rectificada": 21.0,
        "tipo_impositivo": 21.0,
    }
    com = {
        "num_serie_factura": "R-BAD",
        "tipo_factura": "R5",
        "base_imponible": 50.0, "cuota_repercutida": 10.50,
        "importe_total": 60.50,
        "tipo_impositivo": 21.0,
    }
    diff = diff_facturas(sii, com)
    diff_reales = {k: v for k, v in diff.items() if not k.startswith("_")}
    assert diff_reales, (
        "Rectificativa con importes distintos debería reportar discrepancia. "
        f"Diff: {diff}"
    )


# ---------------------------------------------------------------------------
# 5. Parser CSV Newman lee columnas rectificativas
# ---------------------------------------------------------------------------
def test_parser_csv_newman_lee_rectificativos():
    """CSV con las 3 columnas nuevas se parsea correctamente."""
    from router_facturas import _parsear_csv_newman  # noqa: WPS433

    csv = (
        "PeriodoEjercicio|PeriodoPeriodo|IDEmisorFacturaNIF|IDEmisorFacturaNombre|"
        "NumSerieFacturaEmisor|NumSerieFacturaEmisorFin|"
        "FechaExpedicionFacturaEmisor|"
        "TipoFactura|ClaveRegimenEspecial|ImporteTotal|DescripcionOperacion|"
        "FechaOperacion|BaseImponible|TipoImpositivo|CuotaRepercutida|"
        "CausaExencion|"
        "ContraparteNIF|ContraparteNombre|EstadoFactura|"
        "CSVAEAT|NumRegistroPresentacion|TimestampPresentacion|"
        "TipoRectificativa|BaseRectificada|CuotaRectificada\n"
        "2026|06|A74251836|TotalEnergies SLU|"
        "2TSS260600000007||"
        "09-06-2026|"
        "R5|01|0|Facturación de Energía|"
        "09-11-2025|0.00|21|0.00|"
        "||"
        "|Correcta|"
        "2026060918182022|1234567890|09-06-2026 18:19:08|"
        "S|80.33|16.87\n"
    ).encode("utf-8")

    filas, errores, debug = _parsear_csv_newman(
        csv, "A74251836", "TotalEnergies SLU",
    )
    assert not errores, f"Errores inesperados: {errores}"
    assert len(filas) == 1, f"Se esperaba 1 fila, obtenidas {len(filas)}"
    row = filas[0]
    assert row.get("tipo_factura") == "R5"
    assert row.get("tipo_rectificativa") == "S"
    assert row.get("base_rectificada") == pytest.approx(80.33)
    assert row.get("cuota_rectificada") == pytest.approx(16.87)
    assert row.get("num_serie_factura") == "2TSS260600000007"
    # Verifica que _canonical_amount cuadra desde la fila parseada
    assert _canonical_amount(row) == pytest.approx(97.20, abs=0.01)
