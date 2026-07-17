"""Test iter22: fallback `importe_total = base + cuota` cuando la AEAT
NO lo devuelve.

Bug reportado por el usuario: factura exenta consultada por período (Newman)
no traía importe_total → se guardaba `None` en BD → no aparecía en la
Comparativa. La consulta individual sí lo devuelve porque es otro endpoint.

Este test reproduce el XML EXACTO enviado por el usuario:
  1600370068 (A95000295) · Sujeta.Exenta.DetalleExenta con CausaExencion=E4
  BaseImponible=43409.5 · sin <ImporteTotal>
"""

from types import SimpleNamespace as NS


def _make_factura_exenta_e4():
    """Simula el objeto zeep para el XML del usuario."""
    return NS(
        DatosFacturaEmitida=NS(
            TipoFactura="F1",
            FechaOperacion="09-06-2026",
            ClaveRegimenEspecialOTrascendencia="01",
            DescripcionOperacion="Autofactura emitida",
            Contraparte=NS(NombreRazon="ENAGAS GTS,S.A.U.", NIF="A86484292"),
            # NO se establece ImporteTotal — la AEAT no lo devuelve
            TipoDesglose=NS(
                DesgloseFactura=NS(
                    Sujeta=NS(
                        Exenta=NS(
                            DetalleExenta=[
                                NS(CausaExencion="E4", BaseImponible=43409.5),
                            ],
                        ),
                        NoExenta=None,
                    ),
                ),
                DesgloseTipoOperacion=None,
            ),
        ),
        IDFactura=NS(
            NumSerieFacturaEmisor="1600370068",
            FechaExpedicionFacturaEmisor="09-06-2026",
        ),
    )


def test_importe_total_calculado_para_factura_exenta():
    """Antes: `getattr(df, "ImporteTotal", None)` → None. Ahora: fallback
    a `base + cuota` = 43409.5 + 0 = 43409.5."""
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")

    from sii_client import _extraer_factura_canonica

    factura = _make_factura_exenta_e4()
    entrada = NS(
        num_serie_factura="1600370068",
        fecha_expedicion="09-06-2026",
        nif_emisor="A95000295",
        nombre_emisor="TotalEnergies Clientes S.A.U.",
        ejercicio="2026",
        periodo="06",
        nif_titular="A95000295",
    )
    res = _extraer_factura_canonica(factura, entrada)
    assert res["base_imponible"] == 43409.5, (
        f"base_imponible incorrecto: {res['base_imponible']}"
    )
    # Cuota debe ser 0 (o None): factura exenta no tiene cuota.
    assert res["cuota_repercutida"] in (0, 0.0, None), (
        f"cuota_repercutida esperada 0/None: {res['cuota_repercutida']}"
    )
    # ANTES devolvía None. AHORA debe ser 43409.5.
    assert res["importe_total"] == 43409.5, (
        f"importe_total mal (bug reintroducido?): {res['importe_total']} "
        "— para exentas la AEAT no manda <ImporteTotal>, debe calcularse "
        "como base + cuota."
    )


def test_importe_total_no_pisado_si_aeat_lo_manda():
    """Regresión: si la AEAT SÍ manda <ImporteTotal> (consulta individual),
    NO lo sobrescribimos con base+cuota."""
    import sys
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    sys.path.insert(0, "/app/backend")

    from sii_client import _extraer_factura_canonica

    factura = _make_factura_exenta_e4()
    # La AEAT explícitamente da 43410 (podría diferir por redondeos internos)
    factura.DatosFacturaEmitida.ImporteTotal = 43410
    entrada = NS(
        num_serie_factura="1600370068",
        fecha_expedicion="09-06-2026",
        nif_emisor="A95000295",
        nombre_emisor="X",
        ejercicio="2026",
        periodo="06",
        nif_titular="A95000295",
    )
    res = _extraer_factura_canonica(factura, entrada)
    assert res["importe_total"] == 43410, (
        "Cuando AEAT manda ImporteTotal, es la fuente de verdad — no lo "
        "recalculamos"
    )
