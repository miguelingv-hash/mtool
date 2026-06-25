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

# Campos donde TIENE sentido invertir el signo (importes monetarios).
# `tipo_impositivo` queda fuera adrede: el % IVA es siempre positivo por
# convención contable, aunque la base/cuota lleguen del comercial en negativo
# (notas de abono, salidas).
CAMPOS_INVERTIBLES_SIGNO: set[str] = {
    "base_imponible",
    "cuota_repercutida",
    "importe_total",
}


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


def _key_tramo(linea: dict) -> tuple:
    """Clave de emparejamiento entre líneas de detalle_iva.

    Empareja por `tipo_impositivo`. Para líneas exentas (tipo None), usa
    `causa_exencion`. Si tampoco hay causa, se usa el índice como fallback
    (la propia clave no se usa para fallback; ese caso se ordena por orden).
    """
    tipo = linea.get("tipo_impositivo")
    if tipo is not None:
        try:
            return ("tipo", round(float(tipo), 2))
        except (TypeError, ValueError):
            pass
    causa = linea.get("causa_exencion")
    if causa:
        return ("exenta", str(causa))
    return ("otro", None)


def _diff_tramos(
    sii_lineas: list[dict] | None,
    com_lineas: list[dict] | None,
    invertir: bool,
) -> list[dict]:
    """Compara dos listas de detalle_iva. Empareja por `_key_tramo`.

    Estrategia en 2 pasadas para tolerar que SAP/SIGLO no exporten siempre la
    `causa_exencion`:

    1. **Match exacto**: empareja `("tipo", X)` con `("tipo", X)` y
       `("exenta", causa)` con `("exenta", causa)`. Cualquier `("otro", None)`
       (línea sin tipo ni causa) solo encaja con otra `("otro", None)`.
    2. **Match relajado**: las líneas SII exentas restantes (`("exenta", *)`)
       se emparejan con cualquier línea comercial sin tipo y sin causa
       (`("otro", None)`) por orden de aparición. Esto cubre el caso SAP, que
       reporta exentas como simples líneas con `tipo_impositivo=None` sin causa.

    Devuelve una lista con la forma:
      {
        key: {"tipo": 21.0} | {"causa_exencion": "E1"} | {},
        sii: { base, cuota } | None,
        comercial: { base, cuota } | None,
        diff: bool,
      }
    """
    sii = list(sii_lineas or [])
    com_pool = list(com_lineas or [])

    def _norm(v):
        if v is None:
            return None
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return v

    def _build(s, c):
        s_base = _norm(s.get("base_imponible")) if s else None
        s_cuota = _norm(s.get("cuota_repercutida")) if s else None
        c_base = _norm(c.get("base_imponible")) if c else None
        c_cuota = _norm(c.get("cuota_repercutida")) if c else None
        if invertir and c_base is not None:
            c_base = -c_base
        if invertir and c_cuota is not None:
            c_cuota = -c_cuota
        # Clave informativa: prioriza SII y si no comercial
        if s:
            ks = _key_tramo(s)
        else:
            ks = _key_tramo(c) if c else ("otro", None)
        key_info: dict = {}
        if ks[0] == "tipo":
            key_info = {"tipo": ks[1]}
        elif ks[0] == "exenta":
            key_info = {"causa_exencion": ks[1]}
        diff = (c is None) or (s is None) or s_base != c_base or s_cuota != c_cuota
        return {
            "key": key_info,
            "sii": {"base_imponible": s_base, "cuota_repercutida": s_cuota} if s else None,
            "comercial": (
                {"base_imponible": c_base, "cuota_repercutida": c_cuota}
                if c is not None else None
            ),
            "diff": diff,
        }

    out: list[dict] = []
    pending_sii: list[dict] = []

    # Pasada 1: match exacto
    for s in sii:
        ks = _key_tramo(s)
        match_idx = None
        for i, c in enumerate(com_pool):
            if _key_tramo(c) == ks:
                match_idx = i
                break
        if match_idx is not None:
            out.append(_build(s, com_pool.pop(match_idx)))
        else:
            pending_sii.append(s)

    # Pasada 2: SII exentas restantes contra comercial "otro" (sin tipo ni causa)
    for s in pending_sii:
        ks = _key_tramo(s)
        match_idx = None
        if ks[0] == "exenta":
            for i, c in enumerate(com_pool):
                if _key_tramo(c) == ("otro", None):
                    match_idx = i
                    break
        if match_idx is not None:
            out.append(_build(s, com_pool.pop(match_idx)))
        else:
            out.append(_build(s, None))

    # Comercial que quedó sin pareja
    for c in com_pool:
        out.append(_build(None, c))
    return out


def _hay_desglose(d: dict | None) -> bool:
    """¿La factura tiene desglose IVA con >= 1 tramo?"""
    if not d:
        return False
    det = d.get("detalle_iva")
    return bool(det) and len(det) > 0


def diff_facturas(
    a: Optional[dict],
    b: Optional[dict],
    config: Optional[dict] = None,
) -> dict:
    """Compara dos snapshots de factura y devuelve los campos con diferencias.

    `a` = SII, `b` = comercial.

    Estricto: cualquier diferencia (incluyendo ``None`` vs valor) cuenta. Los
    importes se comparan con `==` tras normalizar a float y, opcionalmente,
    invirtiendo el signo del comercial según `config.invertir_signo_por_origen`.

    **Comparación por tramos de IVA** (cuando ambos lados tienen `detalle_iva`):
    `base_imponible`, `tipo_impositivo` y `cuota_repercutida` a nivel cabecera
    NO entran en el diff. En su lugar se compara cada línea del detalle por
    `tipo_impositivo` (líneas exentas por `causa_exencion`). El resultado
    aparece como `diffs["detalle_iva"] = [tramos]` con la forma documentada en
    `_diff_tramos`.
    """
    a = a or {}
    b = b or {}
    cfg = config or {}
    campos_comp: list[str] = cfg.get("campos_comparados") or CAMPOS_COMPARADOS_DEFAULT
    inv_map: dict = cfg.get("invertir_signo_por_origen") or {}
    invertir = bool(inv_map.get(b.get("origen_comercial")))

    # ¿Aplicamos comparación línea a línea?
    detalle_mode = _hay_desglose(a) and _hay_desglose(b)
    # Campos de cabecera que NO comparamos cuando hay desglose (los líneas los
    # gestionan, sumas a nivel cabecera son redundantes y ruidosas).
    campos_skip_si_desglose = {"base_imponible", "tipo_impositivo", "cuota_repercutida"}

    diffs: dict = {}
    for campo in campos_comp:
        if detalle_mode and campo in campos_skip_si_desglose:
            continue
        va = a.get(campo)
        vb = b.get(campo)
        if campo in CAMPOS_NUMERICOS:
            va = _parse_amount(va) if va is not None else None
            vb = _parse_amount(vb) if vb is not None else None
            if invertir and vb is not None and campo in CAMPOS_INVERTIBLES_SIGNO:
                vb = -vb
        if va != vb:
            diffs[campo] = {"sii": va, "comercial": vb}

    if detalle_mode:
        tramos = _diff_tramos(a.get("detalle_iva"), b.get("detalle_iva"), invertir)
        # Solo añadimos detalle_iva al diff si hay al menos un tramo con discrepancia.
        if any(t["diff"] for t in tramos):
            diffs["detalle_iva"] = tramos
    return diffs
