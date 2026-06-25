"""Test del fix de precisión float en sumas de DetalleIVA del SII."""
import sys
sys.path.insert(0, "/app/backend")

import pytest

from router_facturas import _sumar_detalle_iva, _extraer_iva_emitida


class _Fake:
    """Simula los objetos zeep (acceso por atributos)."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def make_detalle(base=None, cuota=None, tipo=None):
    return _Fake(BaseImponible=base, CuotaRepercutida=cuota, TipoImpositivo=tipo)


def make_detalle_exenta(base=None, causa=None):
    return _Fake(BaseImponible=base, CausaExencion=causa)


class TestSumarDetalleIVA:
    def test_caso_real_3_87_menos_0_01(self):
        """Caso reportado por usuario: NoExenta 3.87 + Exenta -0.01 = 3.86
        (sin '3.8600000000000003' por error de precisión)."""
        sin_desg = _Fake(
            Sujeta=_Fake(
                NoExenta=_Fake(
                    DesgloseIVA=_Fake(
                        DetalleIVA=[make_detalle(base=3.87, cuota=0.81, tipo=21)]
                    )
                ),
                Exenta=_Fake(DetalleExenta=[make_detalle_exenta(base=-0.01, causa="E1")]),
            )
        )
        base, cuota, tipo, lineas = _sumar_detalle_iva(sin_desg)
        assert base == 3.86, f"base esperaba 3.86, obtuve {base!r}"
        assert cuota == 0.81
        assert tipo == 21.0
        assert len(lineas) == 2

    def test_multiples_tramos_iva(self):
        """Tramos 21 + 10 en la misma factura: bases se suman con redondeo."""
        sin_desg = _Fake(
            Sujeta=_Fake(
                NoExenta=_Fake(
                    DesgloseIVA=_Fake(
                        DetalleIVA=[
                            make_detalle(base=100.10, cuota=21.02, tipo=21),
                            make_detalle(base=50.05, cuota=5.01, tipo=10),
                        ]
                    )
                )
            )
        )
        base, cuota, tipo, lineas = _sumar_detalle_iva(sin_desg)
        assert base == 150.15
        assert cuota == 26.03
        assert tipo == 21.0  # primer tramo encontrado
        assert len(lineas) == 2

    def test_solo_exenta(self):
        """Factura solo exenta: cuota=0, base=suma de exentas."""
        sin_desg = _Fake(
            Sujeta=_Fake(
                Exenta=_Fake(
                    DetalleExenta=[
                        make_detalle_exenta(base=100.10, causa="E1"),
                        make_detalle_exenta(base=200.20, causa="E2"),
                    ]
                ),
                NoExenta=None,
            )
        )
        base, cuota, tipo, lineas = _sumar_detalle_iva(sin_desg)
        assert base == 300.30
        assert cuota == 0.0
        assert tipo is None
        assert len(lineas) == 2

    def test_sin_sujeta(self):
        sin_desg = _Fake(Sujeta=None)
        base, cuota, tipo, lineas = _sumar_detalle_iva(sin_desg)
        assert base == 0.0  # 0.0 redondeado sigue siendo 0.0
        assert cuota == 0.0
        assert tipo is None
        assert lineas == []

    def test_precision_extrema(self):
        """0.1 + 0.2 = 0.30000000000000004 en float; nuestro round lo arregla."""
        sin_desg = _Fake(
            Sujeta=_Fake(
                NoExenta=_Fake(
                    DesgloseIVA=_Fake(
                        DetalleIVA=[
                            make_detalle(base=0.1, cuota=0.02, tipo=21),
                            make_detalle(base=0.2, cuota=0.04, tipo=21),
                        ]
                    )
                )
            )
        )
        base, cuota, _, _ = _sumar_detalle_iva(sin_desg)
        assert base == 0.30
        assert cuota == 0.06


class TestExtraerIvaEmitida:
    def test_redondeo_tras_agregacion_multinivel(self):
        """DesgloseFactura + PrestacionServicios + Entrega → sumas redondeadas."""
        # Mock: factura con DesgloseFactura.Sujeta y DesgloseTipoOperacion.Entrega
        df = _Fake(
            TipoDesglose=_Fake(
                DesgloseFactura=_Fake(
                    Sujeta=_Fake(
                        NoExenta=_Fake(
                            DesgloseIVA=_Fake(
                                DetalleIVA=[make_detalle(base=10.10, cuota=2.12, tipo=21)]
                            )
                        )
                    )
                ),
                DesgloseTipoOperacion=_Fake(
                    Entrega=_Fake(
                        Sujeta=_Fake(
                            NoExenta=_Fake(
                                DesgloseIVA=_Fake(
                                    DetalleIVA=[make_detalle(base=5.05, cuota=1.06, tipo=21)]
                                )
                            )
                        )
                    ),
                    PrestacionServicios=None,
                ),
            )
        )
        base, cuota, tipo, det = _extraer_iva_emitida(df)
        assert base == 15.15
        assert cuota == 3.18
        assert tipo == 21.0
        assert len(det) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
