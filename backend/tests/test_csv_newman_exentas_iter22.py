"""Test iter22: parser CSV Newman procesa facturas exentas correctamente.

Bug reportado: el script Postman `AEAT_SII_Loop` sólo buscaba `<DetalleIVA>`
en el XML SOAP. Las facturas exentas de la AEAT vienen en
`Sujeta.Exenta.DetalleExenta` (con `CausaExencion` + `BaseImponible`, sin
cuota ni tipo). Al no encontrarse, el CSV emitía celdas vacías y el importer
guardaba `base=None, cuota=None, importe=None` → invisibles en Comparativa.

Fix aplicado en 3 puntos:
  1. `AEAT_SII_Loop.postman_collection.json` — extrae DetalleExenta y
     reconstruye ImporteTotal = base + cuota cuando la AEAT no lo manda.
  2. `router_facturas._NEWMAN_COLUMN_MAP` — mapea CausaExencion.
  3. `router_facturas.py:2006` y `sii_client.py:202` — fallback
     importe_total = base + cuota (para consultas SOAP directas).

Este test simula el nuevo CSV que emite el script arreglado.
"""

import sys

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")


def test_parsear_csv_newman_exenta_e4():
    """CSV con nueva columna CausaExencion + base=43409.5, cuota=0,
    importe=43409.5 (calculado por el script Postman)."""
    from router_facturas import _parsear_csv_newman

    csv_bytes = (
        b"PeriodoEjercicio|PeriodoPeriodo|IDEmisorFacturaNIF|IDEmisorFacturaNombre|"
        b"NumSerieFacturaEmisor|NumSerieFacturaEmisorFin|FechaExpedicionFacturaEmisor|"
        b"TipoFactura|ClaveRegimenEspecial|ImporteTotal|DescripcionOperacion|"
        b"FechaOperacion|BaseImponible|TipoImpositivo|CuotaRepercutida|"
        b"CausaExencion|"
        b"ContraparteNIF|ContraparteNombre|EstadoFactura|"
        b"CSVAEAT|NumRegistroPresentacion|TimestampPresentacion\n"
        b"2026|06|A95000295|TotalEnergies Clientes S.A.U.|"
        b"1600370068||09-06-2026|F1|01|43409.5|Autofactura emitida|"
        b"09-06-2026|43409.5|0|0|"
        b"E4|"
        b"A86484292|ENAGAS GTS,S.A.U.|Correcta|"
        b"2026061709541704||17-06-2026 09:54:17\n"
    )
    filas, errores, debug = _parsear_csv_newman(
        csv_bytes, "A95000295", "TotalEnergies Clientes S.A.U.",
    )
    assert errores == [] or all("Fila 2" not in e for e in errores), (
        f"Errores inesperados: {errores}"
    )
    assert len(filas) == 1, f"Filas: {len(filas)}"
    f = filas[0]
    assert f["num_serie_factura"] == "1600370068"
    assert f["base_imponible"] == 43409.5, f"base={f['base_imponible']}"
    assert f["cuota_repercutida"] in (0, 0.0), f"cuota={f['cuota_repercutida']}"
    assert f["importe_total"] == 43409.5, f"importe={f['importe_total']}"
    assert f.get("causa_exencion") == "E4", (
        f"causa_exencion no persistió: {f.get('causa_exencion')}"
    )


def test_parsear_csv_newman_no_exenta_sigue_funcionando():
    """Regresión: factura normal con DetalleIVA sigue parseándose bien."""
    from router_facturas import _parsear_csv_newman

    csv_bytes = (
        b"PeriodoEjercicio|PeriodoPeriodo|IDEmisorFacturaNIF|IDEmisorFacturaNombre|"
        b"NumSerieFacturaEmisor|NumSerieFacturaEmisorFin|FechaExpedicionFacturaEmisor|"
        b"TipoFactura|ClaveRegimenEspecial|ImporteTotal|DescripcionOperacion|"
        b"FechaOperacion|BaseImponible|TipoImpositivo|CuotaRepercutida|"
        b"CausaExencion|"
        b"ContraparteNIF|ContraparteNombre|EstadoFactura|"
        b"CSVAEAT|NumRegistroPresentacion|TimestampPresentacion\n"
        b"2026|06|A95000295|TotalEnergies Clientes S.A.U.|"
        b"1600000001||09-06-2026|F1|01|121|Venta gas|"
        b"09-06-2026|100|21|21|"
        b"|"
        b"A99999999|CLIENTE X|Correcta|"
        b"2026061709541705||17-06-2026 09:54:17\n"
    )
    filas, _, _ = _parsear_csv_newman(csv_bytes, "A95000295", "")
    assert len(filas) == 1
    f = filas[0]
    assert f["base_imponible"] == 100
    assert f["cuota_repercutida"] == 21
    assert f["importe_total"] == 121
    assert f["tipo_impositivo"] == 21
    # CausaExencion vacía en factura normal → no debe estar en el doc
    assert not f.get("causa_exencion"), (
        f"causa_exencion no debe estar en factura normal: {f.get('causa_exencion')}"
    )
