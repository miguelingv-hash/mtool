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
    # iter31 (2026-02): rectificativas por Sustitución. AEAT devuelve base
    # y cuota reales bajo <ImporteRectificacion>. En Sustitución, el
    # <DesgloseIVA> viene a 0 y el importe económico real está aquí.
    tipo_rectificativa: Optional[str] = None  # "S"|"I" (Sustitución/Diferencia)
    base_rectificada: Optional[float] = None
    cuota_rectificada: Optional[float] = None


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
        # Normaliza -0.0 → 0.0 (IEEE-754 tras invertir signo)
        if s_cuota == 0:
            s_cuota = 0.0
        if c_cuota == 0:
            c_cuota = 0.0
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

        # Comparación de cuota: None == 0.0 (en exentas no aplica IVA — equivalente
        # semántico de "sin cuota" en SII y "cuota 0" en comercial).
        def _eq_cuota(a, b):
            if a is None and (b is None or b == 0):
                return True
            if b is None and (a is None or a == 0):
                return True
            return a == b

        diff = (
            (c is None) or (s is None)
            or s_base != c_base
            or not _eq_cuota(s_cuota, c_cuota)
        )
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

    # Orden: por tipo impositivo descendente (21, 10, 4, ...) y exentas al final.
    # Líneas con tipo None van después de las que tienen tipo.
    def _orden(linea: dict) -> tuple:
        key = linea.get("key") or {}
        if "tipo" in key and key["tipo"] is not None:
            return (0, -float(key["tipo"]))  # negativo para descendente
        if "causa_exencion" in key:
            return (1, str(key.get("causa_exencion") or ""))
        return (2, "")
    out.sort(key=_orden)
    return out


def _hay_desglose(d: dict | None) -> bool:
    """¿La factura tiene desglose IVA con >= 1 tramo?"""
    if not d:
        return False
    det = d.get("detalle_iva")
    return bool(det) and len(det) > 0


def _es_rectificativa_sustitucion(doc: dict) -> bool:
    """Devuelve True si el doc es una rectificativa por Sustitución.
    Aplica cuando `tipo_factura ∈ R1..R5` y `tipo_rectificativa == 'S'`.
    En ese caso el desglose (base/cuota top-level) viene a 0 y el importe
    económico real está en `base_rectificada` / `cuota_rectificada`.
    """
    tipo = str(doc.get("tipo_factura") or "").strip().upper()
    if not tipo.startswith("R"):
        return False
    tipo_rect = str(doc.get("tipo_rectificativa") or "").strip().upper()
    return tipo_rect == "S"


def _base_efectiva(doc: dict) -> Optional[float]:
    """Base imponible efectiva. Para R por Sustitución devuelve la base
    rectificada; para el resto, la base_imponible normal."""
    if _es_rectificativa_sustitucion(doc):
        return _parse_amount(doc.get("base_rectificada"))
    return _parse_amount(doc.get("base_imponible"))


def _cuota_efectiva(doc: dict) -> Optional[float]:
    """Cuota efectiva. Ver `_base_efectiva`."""
    if _es_rectificativa_sustitucion(doc):
        return _parse_amount(doc.get("cuota_rectificada"))
    return _parse_amount(doc.get("cuota_repercutida"))


def _canonical_amount(doc: dict) -> float:
    """Importe canónico de un doc factura (iter28 + iter31).

    - Si es R por Sustitución (`tipo_rectificativa='S'`) →
      `base_rectificada + cuota_rectificada` (el importe económico real
      vive ahí; `importe_total` y desglose vienen a 0 en Sustitución).
    - Sino, si `importe_total` existe y != 0 → devuelve `importe_total`.
    - Sino → devuelve `base + cuota`.

    Rationale: `importe_total` es la verdad económica de la factura,
    incluyendo partes exentas o no sujetas. `base + cuota` es un desglose
    contable que puede diferir si hay líneas exentas / no sujetas.
    """
    if _es_rectificativa_sustitucion(doc):
        br = _parse_amount(doc.get("base_rectificada")) or 0.0
        cr = _parse_amount(doc.get("cuota_rectificada")) or 0.0
        # Fallback: si ambos son 0 (dato ausente), cae al comportamiento
        # normal para no romper canónicos de docs incompletos.
        if abs(br) > 0.01 or abs(cr) > 0.01:
            return br + cr
    importe_total = _parse_amount(doc.get("importe_total")) or 0.0
    if abs(importe_total) > 0.01:
        return importe_total
    b = _parse_amount(doc.get("base_imponible")) or 0.0
    c = _parse_amount(doc.get("cuota_repercutida")) or 0.0
    return b + c


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

    **Fallback por importe canónico (iter27)**: cuando un lado no tiene
    desglose (base+cuota ≈ 0) pero sí tiene `importe_total`, y el importe
    canónico (base+cuota o importe_total según proceda) cuadra con el otro
    lado con tolerancia 0.01€, la factura se marca como coincide global
    y NO se reportan diffs en base/cuota/importe. El diccionario devuelto
    incluye la marca `"_reconciliada_por_importe_canonico": True` para
    que la UI muestre un badge informativo.
    """
    a = a or {}
    b = b or {}
    cfg = config or {}
    campos_comp: list[str] = cfg.get("campos_comparados") or CAMPOS_COMPARADOS_DEFAULT
    inv_map: dict = cfg.get("invertir_signo_por_origen") or {}
    invertir = bool(inv_map.get(b.get("origen_comercial")))
    excluir_tipo_cero = bool(cfg.get("excluir_comercial_tipo_iva_cero", True))

    # iter31 (2026-02): rectificativas por Sustitución. AEAT devuelve
    # `base_imponible=0`, `cuota_repercutida=0`, `importe_total=0` y guarda
    # el importe real en `base_rectificada` / `cuota_rectificada`. Para
    # comparar correctamente contra el comercial (que sí guarda el
    # importe rectificado en base/cuota top-level), "promocionamos" los
    # rectificados a top-level en el doc SII antes del diff. NO tocamos
    # `b` (comercial) — el ajuste sólo aplica al lado SII.
    #
    # Extra: SIGLO guarda las R por Sustitución con signo POSITIVO
    # (contrario a las F1/F2 emitidas normales que van con signo negativo).
    # Cuando el flag `invertir_signo_por_origen[SIGLO]=True` está activo,
    # `diff_facturas` compararía `|SII + Com|` en vez de `|SII − Com|` →
    # marcaría falsa discrepancia con Δ=2×importe. Fix: para R por
    # Sustitución desactivamos la inversión localmente. También
    # sintetizamos un `detalle_iva` SII con los rectificados para que la
    # comparación por tramos IVA cuadre.
    if _es_rectificativa_sustitucion(a):
        br = _parse_amount(a.get("base_rectificada"))
        cr = _parse_amount(a.get("cuota_rectificada"))
        if br is not None or cr is not None:
            a = dict(a)  # copia superficial: no mutamos el doc del caller
            a["base_imponible"] = br
            a["cuota_repercutida"] = cr
            # importe_total efectivo = base + cuota rectificados (el
            # original es 0 en Sustitución).
            a["importe_total"] = round(
                (br or 0.0) + (cr or 0.0), 2,
            )
            # Sintetiza detalle_iva a partir del rectificado, tomando el
            # tipo_impositivo del primer tramo si existe (ya que en la R
            # por Sustitución el desglose oficial viene con base=cuota=0
            # pero mantiene el tipo). Si no hay tipo detectable, se usa
            # cuota/base como ratio o simplemente None.
            tipo_orig = None
            det_orig = a.get("detalle_iva")
            if isinstance(det_orig, list) and det_orig:
                tipo_orig = det_orig[0].get("tipo_impositivo")
            if tipo_orig is None and br and abs(br) > 0.01:
                # cuota / base ≈ tipo_impositivo (%)
                tipo_orig = round((cr or 0) / br * 100, 2) if br else None
            a["detalle_iva"] = [{
                "tipo_impositivo": tipo_orig,
                "base_imponible": br,
                "cuota_repercutida": cr,
                "origen": "sii_rectificada",
            }]
        # Fix señalado arriba: neutralizamos la inversión para esta R.
        invertir = False

    # Filtra las líneas comerciales con tipo_impositivo vacío o cero ANTES de
    # comparar (cuando el flag está activo). Estas líneas se consideran "no
    # comparables" — típicas en SAP/SIGLO para conceptos exentos, suplidos o
    # ajustes contables que no aplican a la conciliación con SII.
    if excluir_tipo_cero:
        det_b = b.get("detalle_iva")
        if isinstance(det_b, list) and det_b:
            def _tipo_no_cero(linea):
                t = linea.get("tipo_impositivo")
                if t is None:
                    return False
                try:
                    return float(t) != 0
                except (TypeError, ValueError):
                    return True
            det_filtrado = [l for l in det_b if _tipo_no_cero(l)]
            # Si filtramos algo, recalculamos también `base_imponible` y
            # `cuota_repercutida` a nivel cabecera con la suma del detalle
            # filtrado. Es CRÍTICO porque cuando SII no tiene desglose, la
            # comparación cae a cabecera y los valores pre-calculados del
            # comercial seguirían incluyendo las líneas excluidas.
            if len(det_filtrado) != len(det_b):
                def _sum_field(lineas, campo):
                    s = 0.0
                    for l in lineas:
                        v = l.get(campo)
                        if v is None:
                            continue
                        try:
                            s += float(v)
                        except (TypeError, ValueError):
                            pass
                    return round(s, 2)
                b = {
                    **b,
                    "detalle_iva": det_filtrado,
                    "base_imponible": _sum_field(det_filtrado, "base_imponible"),
                    "cuota_repercutida": _sum_field(det_filtrado, "cuota_repercutida"),
                }
            else:
                b = {**b, "detalle_iva": det_filtrado}

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

    # Fallback iter27: si hay diffs SÓLO en {base_imponible, cuota_repercutida,
    # importe_total} y el importe canónico cuadra (SII vs Comercial con
    # inversión aplicada), la factura se reconcilia globalmente. Retiramos
    # esos campos del diff y marcamos con `_reconciliada_por_importe_canonico`.
    if diffs and not detalle_mode:
        campos_asimetricos = {"base_imponible", "cuota_repercutida", "importe_total"}
        if set(diffs.keys()).issubset(campos_asimetricos):
            can_a = _canonical_amount(a)
            can_b = _canonical_amount(b)
            if invertir:
                can_b = -can_b
            if abs(can_a - can_b) <= 0.01:
                # Reconciliada por importe canónico: retiramos los diffs
                # de base/cuota/importe (siguen visibles como valores pero
                # no cuentan como diff a efectos de estado).
                for k in list(diffs.keys()):
                    if k in campos_asimetricos:
                        del diffs[k]
                # Guardamos los valores canónicos post-inversión (mismo
                # signo/magnitud) para que la UI muestre "SII X ≈ Com X".
                diffs["_reconciliada_por_importe_canonico"] = {
                    "sii_canonical": round(can_a, 2),
                    "comercial_canonical": round(can_b, 2),
                }

    return diffs
