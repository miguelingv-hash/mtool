"""Regression test iter31.2: extractor Newman + parser tolera el envuelto
en comillas simples (`console.log(fila)` en Postman).

Bug reportado por el usuario:
    En 7867 R por Sustitución quedaba `cuota_rectificada=None` en BD.
    El output crudo Newman muestra la fila envuelta en apóstrofos:

        'CSVROW:...|S|40.19|8.44'

    El script `extraer_csv.py` no quitaba las comillas → el último campo
    del CSV llegaba como `8.44'` → `float("8.44'")` fallaba silenciosamente
    y `_parse_amount_es` devolvía None.

Fix (iter31.2):
    - `extraer_csv.reensamblar_marcadores.flush()` limpia comillas
      simples/dobles/backticks en el texto reconstruido.
    - `router_facturas._parse_amount_es` también las limpia como defensa
      en profundidad (por si algún otro paso las introduce).
"""
import sys
sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/scripts")

import pytest
from router_facturas import _parse_amount_es, _parsear_csv_newman
from extraer_csv import normalizar_lineas, reensamblar_marcadores


@pytest.mark.parametrize("raw,expected", [
    ("8.44", 8.44),
    ("8.44'", 8.44),
    ("'40.19", 40.19),
    ("'40.19'", 40.19),
    ('"16.87"', 16.87),
    ("`1234.5`", 1234.5),
    ("1.234,56", 1234.56),
    ("1.234,56'", 1234.56),
    ("", None),
    ("'", None),
    (None, None),
])
def test_parse_amount_tolera_comillas(raw, expected):
    assert _parse_amount_es(raw) == expected


def test_extraer_csv_quita_comillas_envolvente():
    """El output Newman con `console.log(fila)` envuelve la fila entera
    en apóstrofos, y el `│` del box-drawing en cada wrap. El reensamblado
    debe producir la fila limpia sin ni el apóstrofo ni los │."""
    raw = (
        "  │ 'CSVROW:2026|06|A74251836||2TSN260600000280||30-06-202\n"
        "  │ 6|R1|01|0|Facturacion|21-11-2025|0.00|21|0.00||"
        "72187746S|NIURKA|Correcta|CSV|NREG|30-06-2026 16:59:22|"
        "S|40.19|8.44'\n"
    ).splitlines()
    lineas = normalizar_lineas(raw)
    bloques = reensamblar_marcadores(lineas)
    assert len(bloques) == 1
    tipo, contenido = bloques[0]
    assert tipo == "ROW"
    assert not contenido.endswith("'")
    assert not contenido.startswith("'")
    assert contenido.endswith("|S|40.19|8.44")
    # Debe tener 25 campos (los 22 originales + 3 rectificativos)
    assert contenido.count("|") == 24


def test_parser_end_to_end_envuelta_en_comillas():
    """Parsea un CSV donde la fila viene envuelta en apóstrofos
    (simulando lo que `extraer_csv.py` pasaría al parser SI no limpiara,
    para validar que la doble defensa funciona)."""
    csv_bytes = (
        "PeriodoEjercicio|PeriodoPeriodo|IDEmisorFacturaNIF|"
        "IDEmisorFacturaNombre|NumSerieFacturaEmisor|"
        "NumSerieFacturaEmisorFin|FechaExpedicionFacturaEmisor|"
        "TipoFactura|ClaveRegimenEspecial|ImporteTotal|"
        "DescripcionOperacion|FechaOperacion|BaseImponible|"
        "TipoImpositivo|CuotaRepercutida|CausaExencion|"
        "ContraparteNIF|ContraparteNombre|EstadoFactura|CSVAEAT|"
        "NumRegistroPresentacion|TimestampPresentacion|"
        "TipoRectificativa|BaseRectificada|CuotaRectificada\n"
        "2026|06|A74251836||2TSN260600000280||30-06-2026|R1|01|0|"
        "Facturacion|21-11-2025|0.00|21|0.00||72187746S|NIURKA|"
        "Correcta|CSV|NREG|30-06-2026 16:59:22|S|40.19|8.44'\n"
    ).encode("utf-8")
    filas, err, dbg = _parsear_csv_newman(csv_bytes, "A74251836", "T")
    assert len(filas) == 1
    row = filas[0]
    assert row["num_serie_factura"] == "2TSN260600000280"
    assert row["tipo_rectificativa"] == "S"
    assert row["base_rectificada"] == 40.19
    # KEY: la comilla NO debe romper el parseo
    assert row["cuota_rectificada"] == 8.44
