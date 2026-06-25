"""Test del fix de tipo_impositivo concatenado en CSV Newman."""
import pytest

import sys
sys.path.insert(0, "/app/backend")

from router_facturas import _sanear_tipo_y_cuota, _parse_amount_es


class TestSanearTipoYCuota:
    """El bug: el export Newman concatena TipoImpositivo + CuotaRepercutida en
    una sola celda, p.ej. "21" + "1.84" → "211.84". _parse_amount_es lo lee
    como 211.84 (valor inválido para un tipo de IVA legal en España).
    """

    @pytest.mark.parametrize(
        "tipo_in, cuota_in, tipo_out, cuota_out",
        [
            # Casos del bug real (extraídos de la BD de producción)
            (211.84, None, 21.0, 1.84),   # base 8.74 * 0.21 = 1.84
            (210.91, None, 21.0, 0.91),   # base 4.32 * 0.21
            (211.12, None, 21.0, 1.12),   # base 5.35 * 0.21
            (210.03, None, 21.0, 0.03),   # base 0.15 * 0.21
            (210.81, None, 21.0, 0.81),
            # IVA reducido 10%
            (100.5, None, 10.0, 0.5),
            (101.23, None, 10.0, 1.23),
            # IVA superreducido 4%
            (45.5, None, 4.0, 5.5),
            # No concatenado, valor normal — NO se debe tocar
            (21.0, 1.84, 21.0, 1.84),
            (10.0, None, 10.0, None),
            (4.0, 0.5, 4.0, 0.5),
            (0.0, 0.0, 0.0, 0.0),
            # Tipo None — no toca nada
            (None, None, None, None),
            (None, 1.84, None, 1.84),
        ],
    )
    def test_casos_concatenacion(self, tipo_in, cuota_in, tipo_out, cuota_out):
        doc = {"tipo_impositivo": tipo_in, "cuota_repercutida": cuota_in}
        _sanear_tipo_y_cuota(doc)
        assert doc["tipo_impositivo"] == tipo_out, (
            f"tipo: esperaba {tipo_out}, obtuve {doc['tipo_impositivo']}"
        )
        assert doc["cuota_repercutida"] == cuota_out, (
            f"cuota: esperaba {cuota_out}, obtuve {doc['cuota_repercutida']}"
        )

    def test_no_pisa_cuota_existente(self):
        """Si ya hay cuota_repercutida, el saneado solo arregla el tipo y NO
        sobrescribe la cuota existente."""
        doc = {"tipo_impositivo": 211.84, "cuota_repercutida": 1.84}
        _sanear_tipo_y_cuota(doc)
        assert doc["tipo_impositivo"] == 21.0
        assert doc["cuota_repercutida"] == 1.84  # respetada

    def test_valor_muy_anomalo_descarta(self):
        """Si no encaja con ningún tipo IVA conocido, descarta a null."""
        doc = {"tipo_impositivo": 999.99, "cuota_repercutida": None}
        msg = _sanear_tipo_y_cuota(doc)
        # 9 no está en lista de prefijos pero el código intentará "21","10","7","5","4","0"
        # 999.99 → starts with "9" → no match → null
        assert doc["tipo_impositivo"] is None
        assert "null" in (msg or "")

    def test_tipo_no_numerico(self):
        doc = {"tipo_impositivo": "no-es-numero", "cuota_repercutida": None}
        msg = _sanear_tipo_y_cuota(doc)
        assert doc["tipo_impositivo"] is None
        assert msg is not None


class TestParseAmountEs:
    """Confirma que _parse_amount_es es el origen del bug — interpreta '211.84'
    o '211,84' como un float válido. El fix preventivo viene en _sanear, no
    aquí (porque _parse_amount_es es usado para otros campos legítimos)."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("21", 21.0),
            ("21.0", 21.0),
            ("21,00", 21.0),
            ("1.234,56", 1234.56),  # formato español con miles
            ("211.84", 211.84),     # ← el valor del bug entra como número válido
            ("211,84", 211.84),     # idem en formato español
            ("", None),
            (None, None),
            ("no-numero", None),
        ],
    )
    def test_parse(self, raw, expected):
        assert _parse_amount_es(raw) == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
