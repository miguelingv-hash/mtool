"""Regression test for the Newman CSV extraction wrap bug.

Reproduces the bug reported on factura 26TAABN000008806 where the line wrap in
Newman's table output broke the column delimiter `|` at the right edge of a
wrap, concatenating two columns into one (`116.54` + `21` → `116.5421`) and
shifting every subsequent column to the right.

Root cause: `BORDER_RIGHT_RE` treated the pipe `|` (ASCII, U+007C, the actual
column delimiter) as a border character together with the real Newman border
`│` (U+2502). When a wrap ended right after a delimiter, the trailing `|` was
stripped and the next line was concatenated WITHOUT separator.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Allow `import scripts.extraer_csv` without packaging gymnastics
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
sys.path.insert(0, BACKEND_DIR)

from scripts.extraer_csv import extraer  # noqa: E402


# Raw Newman log fragment (verbatim from the user-reported bug)
RAW_NEWMAN_LOG = (
    "\u250c\u2500\u2500\u2500\u2510\n"
    "\u2502 CSVHEAD:NIFTitular||IDEmisorFacturaNIF|IDEmisorFacturaNombre|"
    "NumSerieFacturaEmisor|NumSerieFacturaEmisorFin|FechaExpedicionFacturaEmisor|"
    "TipoFactura|ClaveRegimenEspecial|PeriodoEjercicio|PeriodoPeriodo|"
    "DescripcionOperacion|FechaOperacion|ImporteTotal|BaseImponible|"
    "TipoImpositivo|CuotaRepercutida|ContraparteNIF|ContraparteNombre|"
    "EstadoFactura|CSVAEAT|NumRegistroPresentacion|TimestampPresentacion \u2502\n"
    "\u251c\u2500\u2500\u2500\u2524\n"
    "\u2502 CSVROW:||A95000295||26TAABN000008806||23-06-2026|F1|0 \u2502\n"
    "\u2502 1||Invoice (art. 6, 7.2 and 7.3 RD 1619/2012)||116.54| \u2502\n"
    "\u2502 21|24.47|11392568R|Emiliano Morales Benito|Correcta|20 \u2502\n"
    "\u2502 26062320025267||23-06-2026 20:03:02' \u2502\n"
    "\u2514\u2500\u2500\u2500\u2518\n"
)


def _run_extraer(raw_log: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "export.txt")
        out = os.path.join(tmp, "facturas.csv")
        with open(inp, "w", encoding="utf-8") as f:
            f.write(raw_log)
        rc = extraer(inp, out)
        assert rc == 0, f"extraer falló con rc={rc}"
        with open(out, "r", encoding="utf-8") as f:
            return f.read().splitlines()


def test_wrap_preserves_column_delimiter_between_base_and_tipo():
    """El wrap NO debe pegar BaseImponible y TipoImpositivo en una sola celda."""
    lines = _run_extraer(RAW_NEWMAN_LOG)
    assert len(lines) >= 2, f"Esperaba cabecera + 1 fila, recibí {lines!r}"
    row = lines[1]
    # Bug: aparecía '116.5421|24.47' (sin pipe). Fix: '116.54|21|24.47'.
    assert "116.5421" not in row, (
        f"Wrap pegó dos columnas en una: {row!r}"
    )
    assert "116.54|21|24.47" in row, (
        f"Esperaba secuencia 'base|tipo|cuota' = '116.54|21|24.47' en {row!r}"
    )


def test_wrap_does_not_shift_subsequent_columns():
    """Tras el fix, las columnas siguientes a BaseImponible/TipoImpositivo
    no deben quedar desplazadas. Verificamos la secuencia exacta de valores."""
    lines = _run_extraer(RAW_NEWMAN_LOG)
    row = lines[1]
    # La secuencia 'base|tipo|cuota|nif|nombre|estado|csv|...|ts' debe
    # aparecer correctamente reensamblada (con el delimitador '|' entre
    # 116.54 y 21 preservado). Si el bug reaparece, sería '116.5421|24.47'
    # y el resto de valores quedarían desplazados.
    expected_sequence = (
        "116.54|21|24.47|11392568R|"
        "Emiliano Morales Benito|Correcta|2026062320025267||23-06-2026 20:03:02"
    )
    assert expected_sequence in row, (
        f"La secuencia esperada no aparece. ¿Wrap pegó columnas?\n"
        f"Esperado contiene: {expected_sequence!r}\n"
        f"Fila reensamblada: {row!r}"
    )


def test_wrap_does_not_lose_first_column_pipe():
    """El primer pipe que abre una continuación tampoco debe perderse."""
    # Caso: la línea termina en algo no-pipe y la siguiente empieza por '|'
    raw = (
        "\u2502 CSVHEAD:A|B|C|D \u2502\n"
        "\u2502 CSVROW:foo|bar \u2502\n"
        "\u2502 |baz|qux \u2502\n"
    )
    lines = _run_extraer(raw)
    assert lines[1] == "foo|bar|baz|qux", (
        f"Esperaba foo|bar|baz|qux pero recibí {lines[1]!r}"
    )
