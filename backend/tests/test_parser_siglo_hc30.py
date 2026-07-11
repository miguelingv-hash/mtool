"""Regresión del parser de reports comerciales SIGLO variante HC30.

Este fichero es un extracto de balance donde:
  1. La columna `Soc.` NO contiene el código de sociedad SAP (es la clase
     de asiento: HC30, NC…).
  2. La cabecera usa `Doc.caus.` (SIGLO) pero también `Nº doc.oficial` (SAP)
     — variante híbrida.
  3. El report reintroduce la cabecera dos veces (línea 16 y 80).
  4. La codificación es latin-1 (SAP legacy).

Antes de los fixes de junio 2026 este fichero devolvía `total=0, errores=1`.
Este test asegura que la regresión no reaparezca.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


HEADER_HC30 = (
    "| Soc.|Doc.caus.   |Nº doc.oficial  |Int.cial. |Dat.adic.  "
    "|Fe.doc.or.|Cta.mayor|II|Tp.impos.|   BaseImpon|    Impto.ML|"
)
SEPARATOR = "|" + "-" * 180 + "|"


def _fila(soc: str, doc: str, ns: str, base: str, cuota: str) -> str:
    return (
        f"| {soc:<4}|{doc:<12}|{ns:<16}|91940099  |CLIENTE TEST"
        f"                                                     "
        f"|01.06.2026|47700006 |T7|   21.000|{base:>12}|{cuota:>12}|"
    )


def _mini_report():
    """Simula el HC30 con 2 cabeceras + varias filas + agrupación de docs."""
    return "\n".join([
        "--------------------------------------------------",
        "|Criterios de clasificación|Ascen.|Desc.|Subtotal|",
        "--------------------------------------------------",
        "",
        HEADER_HC30,
        SEPARATOR,
        # Datos primer bloque
        _fila("HC30", "8489485", "1NSN260600011272", "-108,01", "-22,69"),
        _fila("HC30", "8489486", "1NSN260600011334", "-21,80", "-4,58"),
        _fila("NC",   "8489486", "1NSN260600011334", "-15,23", "-3,20"),  # misma factura → agrupa
        # Reaparición de cabecera (paginación del report)
        HEADER_HC30,
        SEPARATOR,
        _fila("HC30", "8489487", "1NSN260600010902", "-60,45", "-12,69"),
        _fila("NC",   "8489488", "1NSN260600010903", "-22,14", "-4,65"),
    ])


@pytest.mark.asyncio
async def test_parser_siglo_hc30_variante_hibrida():
    """El parser SIGLO acepta la variante HC30 con `Nº doc.oficial` y no
    revienta con múltiples cabeceras."""
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "test_hc30_regression")
    from router_facturas import (  # noqa: E402
        _detectar_formato_tabular,
        _parsear_report_tabular,
    )

    text = _mini_report()

    # Detección: debe ser SIGLO (no SAP, no None).
    assert _detectar_formato_tabular(text) == "SIGLO"

    # Catálogo mínimo para el test (SAP=4432 → A95000295)
    catalogo = {
        "4432": {"nif_titular": "A95000295", "nombre_titular": "TotalEnergies Test"},
    }
    regs, errs = _parsear_report_tabular(text, "SIGLO", catalogo_sociedades=catalogo)

    # 4 facturas distintas (11272, 11334, 10902, 10903); la 11334 aparece
    # dos veces en el CSV pero se agrupa.
    nums = {r["num_serie_factura"] for r in regs}
    assert nums == {
        "1NSN260600011272", "1NSN260600011334",
        "1NSN260600010902", "1NSN260600010903",
    }, f"regs={nums} errs={errs}"
    assert len(regs) == 4

    # La factura duplicada agrupa las 2 líneas (base = -21.80 + -15.23):
    agg = next(r for r in regs if r["num_serie_factura"] == "1NSN260600011334")
    assert abs(agg["base_imponible"] - (-37.03)) < 0.01
    assert len(agg["detalle_iva"]) == 2

    # Como Soc. contiene HC30/NC (no mapeadas), aparecen en el aviso pero
    # NO se cuenta como error de fila real (`fila == -1`).
    warns_soc = [e for e in errs if e.get("fila") == -1]
    assert warns_soc, "Debería avisar de Soc no mapeadas"
    # Ninguna fila real da error
    row_errs = [e for e in errs if e.get("fila", 0) > 0]
    assert not row_errs, f"Errores en filas: {row_errs}"


@pytest.mark.asyncio
async def test_detector_no_confunde_siglo_con_sap():
    """Regresión: antes del fix de tokens exactos, un fichero SAP se
    detectaba como SIGLO porque `Doc.caus.` es substring de `Doc.causante`."""
    from router_facturas import _detectar_formato_tabular  # noqa: E402

    sap_report = "\n".join([
        "| Soc.|Doc.causante  |Nº doc.oficial  |Fe.doc.or.|Fe.doc.or.|Tp.impos.|BaseImpon|Impto.ML|",
        "|" + "-" * 90 + "|",
        "| 4432|FAKE          |F001            |01.05.2025|02.05.2025|21,00    |1000,00  |210,00  |",
    ])
    assert _detectar_formato_tabular(sap_report) == "SAP", (
        "Un fichero SAP no debe detectarse como SIGLO"
    )
