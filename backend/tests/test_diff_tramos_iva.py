"""Test de comparación por tramos de IVA en diff_facturas."""
import sys
sys.path.insert(0, "/app/backend")

import pytest

from factura_model import diff_facturas


SII_DESGLOSADA = {
    "num_serie_factura": "26TAABN000008293",
    "base_imponible": 3.86,
    "cuota_repercutida": 0.81,
    "tipo_impositivo": 21.0,
    "detalle_iva": [
        {"tipo_impositivo": 21.0, "base_imponible": 3.87, "cuota_repercutida": 0.81, "origen": "DesgloseFactura"},
        {"tipo_impositivo": None, "base_imponible": -0.01, "cuota_repercutida": None, "causa_exencion": "E1"},
    ],
}

COM_SAP_INVERTIDO = {
    "num_serie_factura": "26TAABN000008293",
    "base_imponible": -3.86,
    "cuota_repercutida": -0.81,
    "tipo_impositivo": 21.0,
    "origen_comercial": "SAP",
    "detalle_iva": [
        {"tipo_impositivo": 21.0, "base_imponible": -3.87, "cuota_repercutida": -0.81, "origen": "SAP"},
        {"tipo_impositivo": None, "base_imponible": 0.01, "cuota_repercutida": 0.0, "causa_exencion": "E1"},
    ],
}

CONFIG_INVERTIR_SAP = {"invertir_signo_por_origen": {"SAP": True}}


class TestDiffPorTramos:
    def test_no_compara_cabecera_si_hay_desglose(self):
        """Cuando ambos tienen detalle_iva, base/cuota/tipo cabecera NO se comparan."""
        d = diff_facturas(SII_DESGLOSADA, COM_SAP_INVERTIDO, CONFIG_INVERTIR_SAP)
        assert "base_imponible" not in d, f"base no debería estar en diff: {d}"
        assert "cuota_repercutida" not in d, f"cuota no debería estar en diff: {d}"
        assert "tipo_impositivo" not in d, f"tipo no debería estar en diff: {d}"

    def test_tramo_21_coincide_tras_invertir_signo(self):
        d = diff_facturas(SII_DESGLOSADA, COM_SAP_INVERTIDO, CONFIG_INVERTIR_SAP)
        tramos = d.get("detalle_iva")
        if tramos is None:
            # Todos los tramos coinciden → no aparece la clave detalle_iva
            return
        # Si aparece, el tramo 21 al menos debe ser diff=False
        t21 = next((t for t in tramos if t["key"].get("tipo") == 21.0), None)
        assert t21 is not None
        assert t21["diff"] is False, f"tramo 21 no debería tener diff: {t21}"

    def test_sin_inversion_de_signo_marca_discrepancia(self):
        d = diff_facturas(SII_DESGLOSADA, COM_SAP_INVERTIDO)  # sin config
        tramos = d["detalle_iva"]
        t21 = next(t for t in tramos if t["key"].get("tipo") == 21.0)
        assert t21["diff"] is True
        assert t21["sii"]["base_imponible"] == 3.87
        assert t21["comercial"]["base_imponible"] == -3.87

    def test_emparejamiento_por_causa_exencion(self):
        """Las exentas se emparejan por causa, no por tipo."""
        sii = {
            "num_serie_factura": "X",
            "detalle_iva": [
                {"tipo_impositivo": None, "base_imponible": 10.0, "causa_exencion": "E1"},
                {"tipo_impositivo": None, "base_imponible": 20.0, "causa_exencion": "E2"},
            ],
        }
        com = {
            "num_serie_factura": "X",
            "detalle_iva": [
                {"tipo_impositivo": None, "base_imponible": 20.0, "causa_exencion": "E2"},
                {"tipo_impositivo": None, "base_imponible": 10.0, "causa_exencion": "E1"},
            ],
        }
        d = diff_facturas(sii, com)
        # Aunque vienen en distinto orden, se emparejan por causa y coinciden
        tramos = d.get("detalle_iva")
        if tramos is not None:
            # Si aparece, todos los tramos deben ser diff=False
            assert all(t["diff"] is False for t in tramos), f"Esperaba todos coincide, obtuve {tramos}"

    def test_solo_un_lado_marca_diff(self):
        """Si una línea solo existe en SII (o solo comercial), debe marcarse diff."""
        sii = {
            "num_serie_factura": "X",
            "detalle_iva": [
                {"tipo_impositivo": 21.0, "base_imponible": 100.0, "cuota_repercutida": 21.0},
                {"tipo_impositivo": 10.0, "base_imponible": 50.0, "cuota_repercutida": 5.0},
            ],
        }
        com = {
            "num_serie_factura": "X",
            "detalle_iva": [
                {"tipo_impositivo": 21.0, "base_imponible": 100.0, "cuota_repercutida": 21.0},
            ],
        }
        d = diff_facturas(sii, com)
        tramos = d["detalle_iva"]
        t10 = next(t for t in tramos if t["key"].get("tipo") == 10.0)
        assert t10["diff"] is True
        assert t10["comercial"] is None  # solo SII

    def test_sin_desglose_compara_cabecera(self):
        """Si NO hay detalle_iva en ningún lado, la comparación es la de antes."""
        sii = {
            "num_serie_factura": "X",
            "base_imponible": 100.0,
            "cuota_repercutida": 21.0,
            "tipo_impositivo": 21.0,
        }
        com = {
            "num_serie_factura": "X",
            "base_imponible": 99.0,
            "cuota_repercutida": 20.0,
            "tipo_impositivo": 21.0,
        }
        d = diff_facturas(sii, com)
        assert "base_imponible" in d
        assert "cuota_repercutida" in d
        assert "tipo_impositivo" not in d  # iguales
        assert "detalle_iva" not in d  # no hay desglose

    def test_desglose_solo_un_lado_compara_cabecera(self):
        """Si solo un lado tiene desglose, comparamos cabecera (no podemos emparejar)."""
        sii = {
            "num_serie_factura": "X",
            "base_imponible": 100.0,
            "tipo_impositivo": 21.0,
            "detalle_iva": [
                {"tipo_impositivo": 21.0, "base_imponible": 100.0, "cuota_repercutida": 21.0},
            ],
        }
        com = {
            "num_serie_factura": "X",
            "base_imponible": 100.0,
            "tipo_impositivo": 21.0,
            "cuota_repercutida": 21.0,
            # sin detalle_iva
        }
        d = diff_facturas(sii, com)
        # Cabecera: SII no tiene cuota_repercutida (None), comercial sí (21.0) → diff
        assert "cuota_repercutida" in d

    def test_cuota_null_sii_equivale_cero_comercial(self):
        """Líneas exentas: SII no envía cuota_repercutida (null) pero comercial puede
        traer 0.0 — semánticamente equivalente, NO debe marcarse discrepancia."""
        sii = {
            "num_serie_factura": "X",
            "detalle_iva": [
                {"tipo_impositivo": 21.0, "base_imponible": 100.0, "cuota_repercutida": 21.0},
                {"tipo_impositivo": None, "base_imponible": -0.01, "cuota_repercutida": None, "causa_exencion": "E1"},
            ],
        }
        com = {
            "num_serie_factura": "X",
            "detalle_iva": [
                {"tipo_impositivo": 21.0, "base_imponible": 100.0, "cuota_repercutida": 21.0},
                {"tipo_impositivo": None, "base_imponible": -0.01, "cuota_repercutida": 0.0},
            ],
        }
        d = diff_facturas(sii, com)
        # Si todos los tramos coinciden, detalle_iva no aparece en diff
        assert "detalle_iva" not in d, f"esperaba sin discrepancias, obtuve {d}"

    def test_orden_descendente_por_tipo_iva(self):
        """Las líneas deben salir ordenadas: 21, 10, 4, ..., exentas al final."""
        sii = {
            "num_serie_factura": "X",
            "detalle_iva": [
                {"tipo_impositivo": 10.0, "base_imponible": 10.0, "cuota_repercutida": 1.0},
                {"tipo_impositivo": None, "base_imponible": 5.0, "cuota_repercutida": None, "causa_exencion": "E2"},
                {"tipo_impositivo": 21.0, "base_imponible": 100.0, "cuota_repercutida": 21.0},
                {"tipo_impositivo": 4.0, "base_imponible": 50.0, "cuota_repercutida": 2.0},
            ],
        }
        com = {
            "num_serie_factura": "X",
            "detalle_iva": [
                # mismas líneas, distintas cuotas para forzar diff y que aparezca detalle_iva
                {"tipo_impositivo": 21.0, "base_imponible": 100.0, "cuota_repercutida": 999.0},
                {"tipo_impositivo": 10.0, "base_imponible": 10.0, "cuota_repercutida": 1.0},
                {"tipo_impositivo": 4.0, "base_imponible": 50.0, "cuota_repercutida": 2.0},
                {"tipo_impositivo": None, "base_imponible": 5.0, "cuota_repercutida": 0.0, "causa_exencion": "E2"},
            ],
        }
        d = diff_facturas(sii, com)
        tramos = d["detalle_iva"]
        tipos_orden = [t["key"].get("tipo") for t in tramos if "tipo" in t["key"]]
        assert tipos_orden == [21.0, 10.0, 4.0], f"esperaba [21,10,4], obtuve {tipos_orden}"
        # La exenta es la última
        assert tramos[-1]["key"].get("causa_exencion") == "E2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
