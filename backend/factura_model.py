"""
Modelo de Factura compartido por las 2 fuentes (SII y CSV comercial).

Cada documento de Mongo (colecciones `facturas_sii` y `facturas_comercial`)
representa **una factura única** identificada por `num_serie_factura`.

Los campos canónicos sirven a la vez como:
  - Schema del documento en Mongo (datos "actuales" = última versión)
  - Cabecera del CSV comercial (mismas columnas)
  - Base para la comparación entre fuentes

Cada vez que llega una actualización se añade un snapshot en `versiones[]`
y los campos top-level se sobrescriben con los nuevos valores.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, List, Optional


FUENTES = Literal["consulta_unitaria", "consulta_mensual", "csv_comercial"]


# Lista de campos canónicos (orden = orden del CSV)
CAMPOS_CANONICOS: list[str] = [
    "num_serie_factura",
    "fecha_expedicion",
    "nif_emisor",
    "nombre_emisor",
    "ejercicio",
    "periodo",
    "nif_titular",
    "contraparte_nif",
    "contraparte_nombre",
    "tipo_factura",
    "clave_regimen_especial",
    "descripcion_operacion",
    "fecha_operacion",
    "base_imponible",
    "tipo_impositivo",
    "cuota_repercutida",
    "importe_total",
]

# Campos numéricos para comparación con tolerancia (siempre estricta)
CAMPOS_NUMERICOS: list[str] = [
    "base_imponible",
    "tipo_impositivo",
    "cuota_repercutida",
    "importe_total",
]


# Campos comparados por defecto. Excluye los que típicamente no aparecen en
# los ficheros comerciales (razón social, descripción operación...). El
# usuario puede sobreescribir esta lista desde Configuración.
CAMPOS_COMPARADOS_DEFAULT: list[str] = [
    "fecha_expedicion",
    "ejercicio",
    "periodo",
    "base_imponible",
    "tipo_impositivo",
    "cuota_repercutida",
    "importe_total",
]


class FacturaDatos(BaseModel):
    """Datos canónicos de una factura. Todos opcionales para que un upsert
    parcial (p.ej. CSV con menos columnas) no rompa la validación."""

    model_config = ConfigDict(extra="ignore")

    num_serie_factura: str
    fecha_expedicion: Optional[str] = None
    nif_emisor: Optional[str] = None
    nombre_emisor: Optional[str] = None
    ejercicio: Optional[str] = None
    periodo: Optional[str] = None
    nif_titular: Optional[str] = None
    contraparte_nif: Optional[str] = None
    contraparte_nombre: Optional[str] = None
    tipo_factura: Optional[str] = None
    clave_regimen_especial: Optional[str] = None
    descripcion_operacion: Optional[str] = None
    fecha_operacion: Optional[str] = None
    base_imponible: Optional[float] = None
    tipo_impositivo: Optional[float] = None
    cuota_repercutida: Optional[float] = None
    importe_total: Optional[float] = None


class FacturaVersion(BaseModel):
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    fuente: FUENTES
    datos: dict


class FacturaDoc(FacturaDatos):
    """Documento de factura en Mongo (con campos extra de auditoría)."""

    fuente_ultima: FUENTES
    ultima_actualizacion: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    versiones: List[FacturaVersion] = []


def _parse_amount(v) -> Optional[float]:
    """Convierte un valor a float aceptando coma o punto decimal y vacío→None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 and s.count(".") >= 1 else s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_factura_row(row: dict) -> dict:
    """Normaliza un dict (de CSV o de la respuesta SII) a los campos canónicos.

    No valida obligatoriedad — eso lo hace FacturaDatos. Convierte importes y
    aplica strip en strings.
    """
    out: dict = {}
    for k in CAMPOS_CANONICOS:
        v = row.get(k)
        if v is None:
            continue
        if k in CAMPOS_NUMERICOS:
            out[k] = _parse_amount(v)
        else:
            s = str(v).strip()
            out[k] = s if s else None
    return out


def diff_facturas(
    a: Optional[dict],
    b: Optional[dict],
    config: Optional[dict] = None,
) -> dict:
    """Compara dos snapshots de factura y devuelve los campos con diferencias.

    `a` = SII, `b` = comercial.

    Estricto: cualquier diferencia (incluyendo ``None`` vs valor) cuenta. Los
    importes se comparan con `==` tras normalizar a float y, opcionalmente,
    invirtiendo el signo del comercial según `config.invertir_signo_por_origen`
    (para ficheros SAP/SIGLO con notas de abono que llegan en negativo y deben
    compararse en positivo con el SII).

    `config` es opcional; si no se pasa, se usa `CAMPOS_COMPARADOS_DEFAULT`
    y no se invierten signos.
    """
    a = a or {}
    b = b or {}
    cfg = config or {}
    campos_comp: list[str] = cfg.get("campos_comparados") or CAMPOS_COMPARADOS_DEFAULT
    inv_map: dict = cfg.get("invertir_signo_por_origen") or {}
    invertir = bool(inv_map.get(b.get("origen_comercial")))

    diffs: dict = {}
    for campo in campos_comp:
        va = a.get(campo)
        vb = b.get(campo)
        if campo in CAMPOS_NUMERICOS:
            va = _parse_amount(va) if va is not None else None
            vb = _parse_amount(vb) if vb is not None else None
            if invertir and vb is not None:
                vb = -vb
        if va != vb:
            diffs[campo] = {"sii": va, "comercial": vb}
    return diffs
