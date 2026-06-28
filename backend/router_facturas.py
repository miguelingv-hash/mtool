"""
Router de gestión de facturas y comparativa SII vs CSV comercial.

Añade:
  POST /api/sii/consulta-mensual    -> consulta SII por periodo, upsert masivo
  POST /api/comercial/csv           -> sube CSV comercial, upsert masivo
  GET  /api/comercial/csv-template  -> plantilla CSV
  GET  /api/facturas/sii            -> listado facturas en BD desde SII
  GET  /api/facturas/comercial      -> listado facturas en BD desde CSV
  GET  /api/comparativa             -> diferencias por num_serie_factura

Las dos consultas SII (unitaria y mensual) escriben en `db.facturas_sii`.
El CSV escribe en `db.facturas_comercial`. La comparativa hace join por
`num_serie_factura` y devuelve los campos con diferencias.
"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Depends
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone
import asyncio
import csv
import io
import time
import uuid

from auth import require_permission

from factura_model import (
    CAMPOS_CANONICOS,
    CAMPOS_COMPARADOS_DEFAULT,
    CAMPOS_NUMERICOS,
    FacturaDatos,
    FacturaVersion,
    diff_facturas,
    normalize_factura_row,
)
from sii_client import ENDPOINTS, WSDL_URL, build_client


router = APIRouter(prefix="/api")


# Mongo BSON document limit es 16MB. Truncamos los XML grandes para que el
# log siempre se pueda persistir aunque la AEAT devuelva 10K facturas.
MAX_XML_LOG = 4 * 1024 * 1024  # 4 MB por campo (cómodo bajo el límite BSON)


def _truncar_xml(xml: str) -> str:
    if not xml or len(xml) <= MAX_XML_LOG:
        return xml or ""
    return (
        xml[:MAX_XML_LOG]
        + f"\n<!-- TRUNCADO: payload original {len(xml)} bytes "
        f"truncado a {MAX_XML_LOG} bytes para entrar en BSON -->"
    )


# Referencias globales que se inyectan desde server.py
_db = None
_logger = None


def init(db, logger):
    global _db, _logger
    _db = db
    _logger = logger


async def cleanup_orphan_jobs():
    """Marca como `failed` los jobs que quedaron en `queued`/`running` tras un
    reinicio del backend (sus workers ya no existen). Se llama en `startup`."""
    if _db is None:
        return
    # Crear índices críticos (idempotente). Sin estos, los upserts masivos
    # hacen collection scan y el bulk_write tarda > 2 minutos por página.
    try:
        await _db.facturas_sii.create_index("num_serie_factura", unique=True)
        await _db.facturas_comercial.create_index("num_serie_factura", unique=True)
        # Indices compuestos para acelerar comparativa y los `distinct` del
        # endpoint /comparativa/periodos. Sin ellos, con 1M+ facturas la
        # consulta tarda > 25 s y el ingress devuelve 502.
        await _db.facturas_sii.create_index([("ejercicio", 1), ("periodo", 1)])
        await _db.facturas_comercial.create_index([("ejercicio", 1), ("periodo", 1)])
        await _db.jobs.create_index("id", unique=True)
        await _db.jobs.create_index([("status", 1), ("created_at", -1)])
    except Exception:  # noqa: BLE001
        _logger.exception("No se pudieron crear los índices al arranque")

    res = await _db.jobs.update_many(
        {"status": {"$in": ["queued", "running"]}},
        {"$set": {
            "status": "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error_message": "Job huérfano: el backend se reinició durante "
                             "la ejecución. Vuelve a lanzar la consulta.",
        }},
    )
    if res.modified_count:
        _logger.warning(
            "Limpiados %d jobs huérfanos al arranque", res.modified_count
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG_DOC_ID = "default"


async def _load_comparativa_config() -> dict:
    """Devuelve la configuración de comparativa (qué campos comparar y si se
    invierte el signo de los importes comerciales por origen).
    Si no existe en BD, devuelve los defaults.
    """
    doc = await _db.comparativa_config.find_one({"_id": _CONFIG_DOC_ID})
    if not doc:
        return {
            "campos_comparados": list(CAMPOS_COMPARADOS_DEFAULT),
            "invertir_signo_por_origen": {},
            "excluir_comercial_base_cero": False,
            "excluir_comercial_tipo_iva_cero": True,
        }
    return {
        "campos_comparados": doc.get(
            "campos_comparados", list(CAMPOS_COMPARADOS_DEFAULT)
        ),
        "invertir_signo_por_origen": doc.get(
            "invertir_signo_por_origen", {}
        ) or {},
        "excluir_comercial_base_cero": bool(
            doc.get("excluir_comercial_base_cero", False)
        ),
        "excluir_comercial_tipo_iva_cero": bool(
            doc.get("excluir_comercial_tipo_iva_cero", True)
        ),
    }


async def upsert_factura(coleccion: str, datos: dict, fuente: str):
    """Inserta o actualiza una factura con histórico de versiones."""
    if not datos.get("num_serie_factura"):
        return
    now = datetime.now(timezone.utc).isoformat()
    version = FacturaVersion(
        timestamp=now, fuente=fuente, datos=datos
    ).model_dump()
    update = {
        "$set": {
            **datos,
            "fuente_ultima": fuente,
            "ultima_actualizacion": now,
        },
        "$push": {"versiones": version},
    }
    await _db[coleccion].update_one(
        {"num_serie_factura": datos["num_serie_factura"]},
        update,
        upsert=True,
    )


async def upsert_facturas_bulk(coleccion: str, datos_list: list, fuente: str):
    """Upsert masivo de facturas en una sola operación `bulk_write`.

    Para jobs mensuales con miles de facturas por página, esto reduce ~10000
    round-trips a 1. NO mantiene histórico de versiones (`$push`) para no
    inflar los documentos: prima la velocidad de descarga sobre la auditoría
    versionada. El histórico sigue disponible para upserts unitarios."""
    from pymongo import UpdateOne  # noqa: WPS433
    if not datos_list:
        return
    now = datetime.now(timezone.utc).isoformat()
    ops = []
    for d in datos_list:
        if not d.get("num_serie_factura"):
            continue
        ops.append(
            UpdateOne(
                {"num_serie_factura": d["num_serie_factura"]},
                {"$set": {
                    **d,
                    "fuente_ultima": fuente,
                    "ultima_actualizacion": now,
                }},
                upsert=True,
            )
        )
    if ops:
        await _db[coleccion].bulk_write(ops, ordered=False)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sii/consulta-mensual")
async def consulta_mensual(
    nif_titular: str = Form(...),
    nombre_titular: str = Form(...),
    ejercicio: str = Form(...),
    periodo: str = Form(...),
    entorno: str = Form("preproduccion"),
    cert_password: Optional[str] = Form(None),
    certificate: Optional[UploadFile] = File(None),
    max_paginas: Optional[int] = Form(None),
):
    """Consulta mensual al SII vía SOAP real con mTLS. El certificado se
    aporta en la petición o se usa el configurado en el servidor.

    El certificado NO se guarda en el servidor.
    """
    cert_bytes = None
    if certificate is not None:
        cert_bytes = await certificate.read()
        if not cert_bytes:
            cert_bytes = None
    facturas: list[dict] = []
    start_ts = datetime.now(timezone.utc)
    log_entry = {
        "id": __import__("uuid").uuid4().hex,
        "timestamp": start_ts.isoformat(),
        "operation": "ConsultaLRFacturasEmitidas.Mensual",
        "endpoint": ENDPOINTS.get(entorno, ""),
        "entorno": entorno,
        "status": "ok",
        "http_status": None,
        "error_message": None,
        "duration_ms": 0,
        "request_xml": "",
        "response_xml": "",
        "nif_titular": nif_titular,
        "nif_emisor": nif_titular,
        "num_serie_factura": None,
        "consulta_id": None,
        "batch_id": None,
    }

    try:
        try:
            client = build_client(
                cert_bytes=cert_bytes, cert_password=cert_password
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        try:
            facturas, req_xml, resp_xml = _consultar_mensual_real(
                client,
                nif_titular,
                nombre_titular,
                ejercicio,
                periodo,
                entorno,
                max_paginas=max_paginas,
            )
            log_entry["request_xml"] = _truncar_xml(req_xml)
            log_entry["response_xml"] = _truncar_xml(resp_xml)
            log_entry["http_status"] = 200
        except Exception as exc:  # noqa: BLE001
            log_entry["status"] = "error"
            log_entry["error_message"] = str(exc)[:2000]
            log_entry["request_xml"] = _truncar_xml(
                getattr(exc, "request_xml", "") or ""
            )
            log_entry["response_xml"] = _truncar_xml(
                getattr(exc, "response_xml", "") or ""
            )
            log_entry["http_status"] = 502
            _logger.exception("Fallo SOAP en consulta mensual real")
            raise HTTPException(502, str(exc)[:1500])

        for f in facturas:
            await upsert_factura("facturas_sii", f, "consulta_mensual")
    finally:
        log_entry["duration_ms"] = int(
            (datetime.now(timezone.utc) - start_ts).total_seconds() * 1000
        )
        # Defensive: trunca por última vez (por si algún campo XML siguiera
        # demasiado grande y rompiera el insert por encima del límite BSON).
        log_entry["request_xml"] = _truncar_xml(log_entry.get("request_xml", ""))
        log_entry["response_xml"] = _truncar_xml(log_entry.get("response_xml", ""))
        try:
            await _db.wslogs.insert_one(log_entry)
        except Exception:  # noqa: BLE001
            _logger.exception("No se pudo guardar log de consulta mensual")

    return {
        "total": len(facturas),
        "ejercicio": ejercicio,
        "periodo": periodo,
        "facturas": facturas,
    }


@router.post("/sii/verificar-completitud")
async def verificar_completitud(
    nif_titular: str = Form(...),
    nombre_titular: str = Form(""),
    ejercicio: str = Form(...),
    periodo: str = Form(...),
    entorno: str = Form("preproduccion"),
    cert_password: Optional[str] = Form(None),
    certificate: Optional[UploadFile] = File(None),
):
    """Verifica si AEAT tiene facturas posteriores a las ya descargadas en BD
    para `(nif, ejercicio, periodo)`. Si las hay, las inserta vía upsert.

    Devuelve `completo: bool` y `nuevas_facturas: N`. Coste mínimo: 1 sola
    llamada SOAP cuando el periodo ya está completo.
    """
    cert_bytes = None
    if certificate is not None:
        cert_bytes = await certificate.read()
        if not cert_bytes:
            cert_bytes = None
    # 1) Buscar la última factura de BD para construir ClavePaginacion
    ult = await _db.facturas_sii.find_one(
        {
            "ejercicio": str(ejercicio),
            "periodo": str(periodo),
            "nif_titular": nif_titular,
        },
        sort=[("num_serie_factura", -1), ("fecha_expedicion", -1)],
        projection={
            "_id": 0,
            "num_serie_factura": 1,
            "fecha_expedicion": 1,
        },
    )
    total_antes = await _db.facturas_sii.count_documents({
        "ejercicio": str(ejercicio),
        "periodo": str(periodo),
        "nif_titular": nif_titular,
    })

    start_clave = None
    if ult:
        start_clave = {
            "IDEmisorFactura": {"NIF": nif_titular},
            "NumSerieFacturaEmisor": ult["num_serie_factura"],
            "FechaExpedicionFacturaEmisor": ult["fecha_expedicion"],
        }

    # 2) Llamar al SII — UNA SOLA PÁGINA (≤ 10K facturas) para no superar
    #    el timeout del ingress. Si la primera página devuelve N>0 facturas
    #    sabemos que el periodo NO estaba completo. El usuario puede luego
    #    lanzar una consulta mensual normal para traer el resto.
    start_ts = datetime.now(timezone.utc)
    facturas: list[dict] = []
    error_msg: Optional[str] = None
    try:
        try:
            client = build_client(
                cert_bytes=cert_bytes, cert_password=cert_password
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        facturas, _req_xml, _resp_xml = await asyncio.to_thread(
            _consultar_mensual_real,
            client,
            nif_titular,
            nombre_titular or nif_titular,
            str(ejercicio),
            str(periodo),
            entorno,
            None,           # progress_cb
            1,              # max_paginas: SÓLO 1 página para no timeout
            start_clave,    # start_clave
        )

        # 3) Persistir nuevas (idempotente: upsert por num_serie_factura)
        if facturas:
            await upsert_facturas_bulk("facturas_sii", facturas, "verificacion_completitud")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)[:1500]
        _logger.exception("Fallo en verificar-completitud")

    total_despues = await _db.facturas_sii.count_documents({
        "ejercicio": str(ejercicio),
        "periodo": str(periodo),
        "nif_titular": nif_titular,
    })

    # 4) Log auditoría (truncado)
    duration_ms = int(
        (datetime.now(timezone.utc) - start_ts).total_seconds() * 1000
    )
    try:
        await _db.wslogs.insert_one({
            "id": uuid.uuid4().hex,
            "timestamp": start_ts.isoformat(),
            "operation": "ConsultaLRFacturasEmitidas.VerificarCompletitud",
            "endpoint": ENDPOINTS.get(entorno, ""),
            "entorno": entorno,
            "status": "error" if error_msg else "ok",
            "http_status": 502 if error_msg else 200,
            "error_message": error_msg,
            "duration_ms": duration_ms,
            "request_xml": "",
            "response_xml": "",
            "nif_titular": nif_titular,
            "nif_emisor": nif_titular,
            "num_serie_factura": None,
            "consulta_id": None,
            "batch_id": None,
            "extra": {
                "ejercicio": str(ejercicio),
                "periodo": str(periodo),
                "nuevas_facturas": len(facturas),
                "total_antes": total_antes,
                "total_despues": total_despues,
                "ultima_bd": ult,
            },
        })
    except Exception:  # noqa: BLE001
        _logger.exception("No se pudo guardar log de verificar-completitud")

    if error_msg:
        raise HTTPException(502, error_msg)

    # Si la única página devuelta venía llena (cap 10K), AEAT puede tener más
    # facturas pendientes después. Avisamos al usuario para que lance una
    # consulta mensual normal y termine de descargarlas.
    PAGE_CAP = 10000
    posiblemente_hay_mas = len(facturas) >= PAGE_CAP

    return {
        "completo": len(facturas) == 0,
        "nuevas_facturas": len(facturas),
        "posiblemente_hay_mas": posiblemente_hay_mas,
        "total_antes": total_antes,
        "total_despues": total_despues,
        "ultima_factura_bd": ult,
        "ejercicio": str(ejercicio),
        "periodo": str(periodo),
    }


def _sumar_detalle_iva(sin_desglose) -> tuple[float, float, float | None, list[dict]]:
    """Suma BaseImponible / CuotaRepercutida de los DetalleIVA dentro de un
    `TipoSinDesgloseType` / `TipoSinDesglosePrestacionType`. Incluye también
    los tramos **Sujeta.Exenta.DetalleExenta** (con `causa_exencion` y sin
    cuota repercutida).
    Devuelve (base, cuota, tipo, detalles) — `tipo` se toma del primer
    DetalleIVA no-exento; `detalles` incluye todas las líneas (no-exentas y
    exentas), cada una con `tipo_impositivo`, `base_imponible`,
    `cuota_repercutida` y opcionalmente `causa_exencion`.
    """
    if sin_desglose is None:
        return 0.0, 0.0, None, []
    sujeta = getattr(sin_desglose, "Sujeta", None)
    if sujeta is None:
        return 0.0, 0.0, None, []
    base_tot = 0.0
    cuota_tot = 0.0
    tipo: float | None = None
    lineas: list[dict] = []

    # 1) Tramos NO exentos (con tipo y cuota)
    no_exenta = getattr(sujeta, "NoExenta", None)
    desg = getattr(no_exenta, "DesgloseIVA", None) if no_exenta else None
    detalles = getattr(desg, "DetalleIVA", None) if desg else None
    for d in detalles or []:
        b = getattr(d, "BaseImponible", None)
        c = getattr(d, "CuotaRepercutida", None)
        t = getattr(d, "TipoImpositivo", None)
        if b is not None:
            base_tot += float(b)
        if c is not None:
            cuota_tot += float(c)
        if tipo is None and t is not None:
            tipo = float(t)
        lineas.append({
            "tipo_impositivo": float(t) if t is not None else None,
            "base_imponible": float(b) if b is not None else None,
            "cuota_repercutida": float(c) if c is not None else None,
        })

    # 2) Tramos EXENTOS (Sujeta.Exenta.DetalleExenta). No tienen cuota ni tipo,
    # pero sí causa de exención (E1..E6) y base imponible. Suman en `base_tot`.
    exenta = getattr(sujeta, "Exenta", None)
    det_ex = getattr(exenta, "DetalleExenta", None) if exenta else None
    for d in det_ex or []:
        b = getattr(d, "BaseImponible", None)
        causa = getattr(d, "CausaExencion", None)
        if b is not None:
            base_tot += float(b)
        lineas.append({
            "tipo_impositivo": None,
            "base_imponible": float(b) if b is not None else None,
            "cuota_repercutida": None,
            "causa_exencion": str(causa) if causa is not None else None,
        })

    # base_tot y cuota_tot son sumas de varios DetalleIVA. Floats binarios
    # acumulan errores de precisión (p.ej. 3.87 + (-0.01) = 3.8600000000000003),
    # así que redondeamos a 2 decimales (importes monetarios).
    return round(base_tot, 2), round(cuota_tot, 2), tipo, lineas


def _extraer_iva_emitida(
    df,
) -> tuple[float | None, float | None, float | None, list[dict]]:
    """Recorre `DatosFacturaEmitida.TipoDesglose` (choice DesgloseFactura |
    DesgloseTipoOperacion.{PrestacionServicios,Entrega}) y devuelve
    (base_imponible, cuota_repercutida, tipo_impositivo, detalle_iva) agregados.
    `detalle_iva` etiqueta cada línea con su origen.
    """
    if df is None:
        return None, None, None, []
    td = getattr(df, "TipoDesglose", None)
    if td is None:
        return None, None, None, []
    base = cuota = 0.0
    tipo: float | None = None
    encontrado = False
    detalle_iva: list[dict] = []
    sin = getattr(td, "DesgloseFactura", None)
    if sin is not None:
        b, c, t, lineas = _sumar_detalle_iva(sin)
        base += b
        cuota += c
        if tipo is None:
            tipo = t
        for l in lineas:
            detalle_iva.append({**l, "origen": "DesgloseFactura"})
        encontrado = True
    con = getattr(td, "DesgloseTipoOperacion", None)
    if con is not None:
        for nombre in ("PrestacionServicios", "Entrega"):
            sub = getattr(con, nombre, None)
            if sub is not None:
                b, c, t, lineas = _sumar_detalle_iva(sub)
                base += b
                cuota += c
                if tipo is None:
                    tipo = t
                for l in lineas:
                    detalle_iva.append({**l, "origen": nombre})
                encontrado = True
    if not encontrado:
        return None, None, None, []
    # Misma razón que en _sumar_detalle_iva: redondeo a 2 decimales tras sumar
    # múltiples desgloses (DesgloseFactura + PrestacionServicios + Entrega).
    return round(base, 2), round(cuota, 2), tipo, detalle_iva


# =============================================================================
# Conciliación con CSV de Newman (R3)
# =============================================================================
# Newman se usa como fuente "verdad" alternativa porque su paginación
# (collection JS) es independiente del worker Python del backend. Si el job
# online ha perdido facturas, el CSV de Newman puede tener las que faltan.
# Estos dos endpoints permiten al usuario subir su CSV, ver el diff con la BD
# y opcionalmente insertar las faltantes.

# Mapeo de cabeceras (XSD AEAT) -> campos canónicos. Idéntico al script CLI
# `ingestar_csv_a_mongo.py`; lo replicamos aquí para no acoplar backend a
# scripts/.
_NEWMAN_COLUMN_MAP: dict[str, str] = {
    "PeriodoEjercicio": "ejercicio",
    "PeriodoPeriodo": "periodo",
    "IDEmisorFacturaNIF": "nif_emisor",
    "IDEmisorFacturaNombre": "nombre_emisor",
    "NumSerieFacturaEmisor": "num_serie_factura",
    "NumSerieFacturaEmisorFin": "num_serie_factura_fin",
    "FechaExpedicionFacturaEmisor": "fecha_expedicion",
    "TipoFactura": "tipo_factura",
    "ClaveRegimenEspecial": "clave_regimen_especial",
    "ImporteTotal": "importe_total",
    "DescripcionOperacion": "descripcion_operacion",
    "FechaOperacion": "fecha_operacion",
    "BaseImponible": "base_imponible",
    "TipoImpositivo": "tipo_impositivo",
    "CuotaRepercutida": "cuota_repercutida",
    "ContraparteNIF": "contraparte_nif",
    "ContraparteNombre": "contraparte_nombre",
    "EstadoFactura": "estado_factura",
    "CSVAEAT": "csv_aeat",
    "NumRegistroPresentacion": "num_registro_presentacion",
    "TimestampPresentacion": "timestamp_presentacion",
}

_NEWMAN_NUMERIC = {
    "importe_total", "base_imponible", "tipo_impositivo", "cuota_repercutida",
}

# Tipos de IVA legales en España (con un pequeño margen por si AEAT introduce
# otros tramos). Cualquier valor fuera de este rango se considera ruido del CSV
# (típicamente por celdas concatenadas en el export de Newman/Postman).
_TIPO_IMPOSITIVO_RANGO_VALIDO = (0.0, 30.0)


def _sanear_tipo_y_cuota(doc: dict) -> Optional[str]:
    """Detecta y mitiga celdas concatenadas en `tipo_impositivo`.

    Caso conocido en exports Newman bug: la celda TipoImpositivo trae el tipo y
    la cuota juntos sin separador, p.ej. "21" + "1.84" → 211.84.
    Si `tipo_impositivo` está fuera del rango legal y `cuota_repercutida` es
    None, intenta reconstruirlo: separa el "21" inicial y deja el resto como
    cuota. Si no se puede, descarta ambos campos (deja None) para evitar
    diferencias espurias en la comparativa.

    Devuelve una descripción del saneado (o None si no fue necesario).
    """
    t = doc.get("tipo_impositivo")
    if t is None:
        return None
    try:
        tf = float(t)
    except (TypeError, ValueError):
        doc["tipo_impositivo"] = None
        return "tipo_impositivo no numérico → null"
    lo, hi = _TIPO_IMPOSITIVO_RANGO_VALIDO
    if lo <= tf <= hi:
        return None
    # Anómalo. Intenta separar prefijo "21" / "10" / "4" / "5" / "7" / "0".
    s = f"{tf:.2f}".rstrip("0").rstrip(".")
    for prefijo in ("21", "10", "7", "5", "4", "0"):
        if s.startswith(prefijo) and len(s) > len(prefijo):
            resto = s[len(prefijo):].lstrip(".")
            try:
                # Reconstruye "1.84" desde "184" o "1.84"
                cuota_val = float(f"0.{resto}") if "." not in resto else float(resto)
                doc["tipo_impositivo"] = float(prefijo)
                if doc.get("cuota_repercutida") is None:
                    doc["cuota_repercutida"] = round(cuota_val, 2)
                return (
                    f"tipo_impositivo anómalo {tf} → {prefijo} + cuota {cuota_val} "
                    f"(celdas Newman concatenadas)"
                )
            except ValueError:
                pass
    doc["tipo_impositivo"] = None
    return f"tipo_impositivo fuera de rango ({tf}) → null"


def _parse_amount_es(raw) -> Optional[float]:
    """Acepta '1.234,56', '1234,56', '1234.56'. Vacío → None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.count(",") == 1 and s.count(".") >= 1:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _norm_periodo(p) -> str:
    """Normaliza un periodo SII: '5' ↔ '05', '1t' → '1T'.
    Devuelve cadena vacía si es None/vacío."""
    if p is None:
        return ""
    s = str(p).strip().upper()
    if not s:
        return ""
    if s.isdigit():
        # 1-12 → '01'..'12'; otros números (raros) también con padding-2
        n = int(s)
        if 1 <= n <= 12:
            return f"{n:02d}"
        return s
    return s


def _parsear_csv_newman(contenido: bytes, nif_titular: str, nombre_titular: str) -> tuple[list[dict], list[str], dict]:
    """Parsea el CSV generado por extraer_csv.py. Devuelve
    (filas_validas, errores, debug_info).

    `debug_info` incluye: delimitador detectado, cabeceras detectadas, total
    filas brutas leídas y un ejemplo de la primera fila ya mapeada. Sirve para
    diagnosticar visualmente cuando el resultado da 0 filas.

    Detecta automáticamente el delimitador (`|` o `,`). Cada fila válida
    se mapea a campos canónicos (mismo schema que facturas_sii).
    """
    try:
        text = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = contenido.decode("latin-1", errors="replace")

    primera_linea = text.split("\n", 1)[0] if text else ""
    delim = "|" if "|" in primera_linea else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    debug: dict = {
        "delimitador": delim,
        "headers_detectadas": list(reader.fieldnames or []),
        "total_filas_brutas": 0,
        "primera_fila_mapeada": None,
        "primera_fila_bruta": None,
    }

    if not reader.fieldnames:
        return [], ["CSV vacío o sin cabecera"], debug

    if "NumSerieFacturaEmisor" not in reader.fieldnames:
        return [], [
            "El CSV no contiene la columna 'NumSerieFacturaEmisor'. "
            f"Cabeceras encontradas: {reader.fieldnames}"
        ], debug

    filas: list[dict] = []
    errores: list[str] = []
    filas_brutas = 0
    for idx, row in enumerate(reader, start=2):  # idx=2 → primera fila de datos
        filas_brutas = idx - 1
        if debug["primera_fila_bruta"] is None:
            # Guardamos un sample de la fila bruta para diagnóstico
            debug["primera_fila_bruta"] = {k: row.get(k) for k in list(row.keys())[:10]}

        doc: dict = {}
        for csv_col, canon in _NEWMAN_COLUMN_MAP.items():
            v = row.get(csv_col)
            if v is None:
                continue
            v = str(v).strip()
            if not v:
                continue
            doc[canon] = _parse_amount_es(v) if canon in _NEWMAN_NUMERIC else v

        if not doc.get("num_serie_factura"):
            errores.append(f"Fila {idx}: num_serie_factura vacío")
            continue
        # Normaliza el periodo del CSV ya en el momento del parseo, para que
        # comparaciones contra filtros del UI ('05') o contra la BD ('05')
        # sean estables aunque AEAT/Newman emitan '5' sin zero-padding.
        if doc.get("periodo"):
            doc["periodo"] = _norm_periodo(doc["periodo"])
        # Saneado: el export Newman tiene un bug por el cual a veces concatena
        # TipoImpositivo + CuotaRepercutida en la misma celda (p.ej. "211.84"
        # cuando debería ser tipo=21, cuota=1.84). Detectamos valores fuera del
        # rango legal y tratamos de reconstruir.
        warn = _sanear_tipo_y_cuota(doc)
        if warn:
            errores.append(f"Fila {idx} ({doc.get('num_serie_factura')}): {warn}")
        doc["nif_titular"] = nif_titular
        if nombre_titular:
            doc["nombre_titular"] = nombre_titular
        filas.append(doc)
        if debug["primera_fila_mapeada"] is None:
            debug["primera_fila_mapeada"] = dict(doc)

    debug["total_filas_brutas"] = filas_brutas

    return filas, errores, debug


@router.post("/sii/conciliar-newman")
async def conciliar_newman(
    file: UploadFile = File(...),
    nif_titular: str = Form(...),
    ejercicio: Optional[str] = Form(None),
    periodo: Optional[str] = Form(None),
    incluir_faltantes_completas: bool = Form(False),
):
    """Sube un CSV (formato Newman extraído por extraer_csv.py) y lo compara
    contra `facturas_sii` filtrado por `(nif_titular, ejercicio, periodo)`.

    No escribe nada en BD. Devuelve un resumen con:
      - total_csv:       filas válidas en el CSV
      - total_bd:        facturas en BD que matchean el filtro
      - faltantes_en_bd: en CSV pero NO en BD (las realmente perdidas)
      - extra_en_bd:     en BD pero NO en CSV (pueden ser anuladas u otros periodos)
      - coinciden:       están en ambos lados
      - errores_csv:     errores de parseo del CSV
      - faltantes_preview: primeras 100 faltantes (num_serie + importe_total)

    Para insertar las faltantes, llama después a `/sii/conciliar-newman/importar-faltantes`.
    """
    contenido = await file.read()
    if not contenido:
        raise HTTPException(400, "El CSV está vacío")

    filas, errores, debug = _parsear_csv_newman(contenido, nif_titular, "")
    if not filas and errores:
        raise HTTPException(400, "; ".join(errores[:3]))

    # Si el CSV no trae ejercicio/periodo (la colección Postman no los rellena
    # porque la respuesta AEAT no los incluye por registro), heredamos los del
    # filtro del UI: asumimos que el CSV cargado pertenece a ese periodo.
    periodo_norm_in = _norm_periodo(periodo) if periodo else ""
    relleno_aplicado = 0
    for f in filas:
        if ejercicio and not f.get("ejercicio"):
            f["ejercicio"] = str(ejercicio)
            relleno_aplicado += 1
        if periodo_norm_in and not f.get("periodo"):
            f["periodo"] = periodo_norm_in
    debug["relleno_filtro_aplicado"] = relleno_aplicado

    # Filtramos en memoria por ejercicio/periodo si vienen.
    if ejercicio:
        filas = [f for f in filas if str(f.get("ejercicio", "")) == str(ejercicio)]
    if periodo:
        filas = [f for f in filas if _norm_periodo(f.get("periodo")) == periodo_norm_in]

    num_series_csv = {f["num_serie_factura"] for f in filas}

    # Carga las facturas de BD para el (nif, ejercicio, periodo)
    filtro_bd: dict = {"nif_titular": nif_titular}
    if ejercicio:
        filtro_bd["ejercicio"] = str(ejercicio)
    if periodo:
        # En BD el periodo se guarda como '05' (normalizado vía consulta SOAP).
        # Pero por seguridad aceptamos ambos formatos.
        periodo_norm = _norm_periodo(periodo)
        filtro_bd["periodo"] = {"$in": [periodo_norm, str(int(periodo_norm)) if periodo_norm.isdigit() else periodo_norm]}

    cursor = _db.facturas_sii.find(
        filtro_bd, {"_id": 0, "num_serie_factura": 1}
    )
    num_series_bd: set[str] = set()
    async for d in cursor:
        ns = d.get("num_serie_factura")
        if ns:
            num_series_bd.add(ns)

    faltantes = num_series_csv - num_series_bd  # En CSV, no en BD = perdidas
    extras = num_series_bd - num_series_csv     # En BD, no en CSV = sobran o de otro periodo
    coinciden = num_series_csv & num_series_bd

    filas_por_ns = {f["num_serie_factura"]: f for f in filas}
    preview = []
    for ns in sorted(faltantes)[:100]:
        f = filas_por_ns.get(ns, {})
        preview.append({
            "num_serie_factura": ns,
            "fecha_expedicion": f.get("fecha_expedicion"),
            "base_imponible": f.get("base_imponible"),
            "importe_total": f.get("importe_total"),
            "estado_factura": f.get("estado_factura"),
        })

    # Lista completa de faltantes con todos los campos canónicos. El frontend
    # la trocea en lotes pequeños al llamar a `/importar-lote`, así no hay
    # timeout independientemente del tamaño.
    #
    # PERO con CSVs gigantes (cientos de miles de filas) devolver TODAS las
    # faltantes en una sola respuesta hincha el JSON a centenas de MB y rompe
    # el ingress. Por defecto NO se devuelven aquí. El frontend puede:
    #   - Pedirlas con `incluir_faltantes_completas=true` (CSVs pequeños), o
    #   - Llamar a `/importar-faltantes` con el CSV (CSV se procesa server-side
    #     entero, sin re-postear los datos al backend).
    if incluir_faltantes_completas:
        faltantes_completas = [filas_por_ns[ns] for ns in sorted(faltantes)]
    else:
        faltantes_completas = []
    faltantes_truncado = False

    extra_preview = sorted(extras)[:20]

    return {
        "filtro": {"nif_titular": nif_titular, "ejercicio": ejercicio, "periodo": periodo},
        "total_csv": len(filas),
        "total_bd": len(num_series_bd),
        "faltantes_en_bd": len(faltantes),
        "extra_en_bd": len(extras),
        "coinciden": len(coinciden),
        "errores_csv": errores[:50],
        "faltantes_preview": preview,
        "faltantes_completas": faltantes_completas,
        "faltantes_truncado": faltantes_truncado,
        "extra_preview": extra_preview,
        "debug": debug,
    }


@router.post("/sii/conciliar-newman/importar-faltantes")
async def conciliar_newman_importar(
    file: UploadFile = File(...),
    nif_titular: str = Form(...),
    nombre_titular: str = Form(""),
    ejercicio: Optional[str] = Form(None),
    periodo: Optional[str] = Form(None),
):
    """Igual que `/sii/conciliar-newman` pero INSERTA en `facturas_sii` las
    facturas del CSV que no estén ya en BD. Usa `upsert_facturas_bulk` con
    `fuente: "conciliacion_newman"` para que sean trazables.

    Idempotente: si todas las facturas del CSV ya están en BD, no inserta nada.
    """
    contenido = await file.read()
    if not contenido:
        raise HTTPException(400, "El CSV está vacío")

    filas, errores, _debug = _parsear_csv_newman(contenido, nif_titular, nombre_titular or "")
    if not filas:
        raise HTTPException(
            400, f"No se pudieron extraer filas válidas del CSV. Errores: {errores[:3]}"
        )

    # Mismo relleno por herencia que en /sii/conciliar-newman.
    periodo_norm_in = _norm_periodo(periodo) if periodo else ""
    for f in filas:
        if ejercicio and not f.get("ejercicio"):
            f["ejercicio"] = str(ejercicio)
        if periodo_norm_in and not f.get("periodo"):
            f["periodo"] = periodo_norm_in

    if ejercicio:
        filas = [f for f in filas if str(f.get("ejercicio", "")) == str(ejercicio)]
    if periodo:
        filas = [f for f in filas if _norm_periodo(f.get("periodo")) == periodo_norm_in]

    num_series_csv = [f["num_serie_factura"] for f in filas]

    base_filtro_bd: dict = {"nif_titular": nif_titular}
    if ejercicio:
        base_filtro_bd["ejercicio"] = str(ejercicio)
    if periodo:
        periodo_norm = _norm_periodo(periodo)
        base_filtro_bd["periodo"] = {"$in": [periodo_norm, str(int(periodo_norm)) if periodo_norm.isdigit() else periodo_norm]}

    # Trocea la query existencial: MongoDB limita las queries a 16MB de BSON.
    # Con un `$in` de cientos de miles de num_serie_factura el filtro supera ese
    # límite y devuelve DocumentTooLarge. Procesamos en chunks de 20k.
    NS_CHUNK = 20_000
    existentes: set[str] = set()
    for i in range(0, len(num_series_csv), NS_CHUNK):
        chunk_ns = num_series_csv[i : i + NS_CHUNK]
        filtro_bd = dict(base_filtro_bd)
        filtro_bd["num_serie_factura"] = {"$in": chunk_ns}
        async for d in _db.facturas_sii.find(filtro_bd, {"_id": 0, "num_serie_factura": 1}):
            existentes.add(d["num_serie_factura"])

    faltantes = [f for f in filas if f["num_serie_factura"] not in existentes]

    if faltantes:
        # Insertamos por lotes de 2000 para no inflar memoria con CSVs grandes.
        batch_size = 2000
        insertadas = 0
        for i in range(0, len(faltantes), batch_size):
            chunk = faltantes[i : i + batch_size]
            await upsert_facturas_bulk(
                "facturas_sii", chunk, "conciliacion_newman"
            )
            insertadas += len(chunk)
    else:
        insertadas = 0

    return {
        "filtro": {"nif_titular": nif_titular, "ejercicio": ejercicio, "periodo": periodo},
        "total_csv": len(filas),
        "ya_en_bd": len(existentes),
        "insertadas": insertadas,
        "errores_csv": errores[:50],
    }


async def _ejecutar_importar_faltantes_job(
    job_id: str,
    contenido: bytes,
    nif_titular: str,
    nombre_titular: str,
    ejercicio: Optional[str],
    periodo: Optional[str],
) -> None:
    """Worker en background del import masivo desde CSV Newman.

    Actualiza `jobs[job_id].progress.{processed, total, phase}` y `status`.
    Reusa la misma lógica del endpoint síncrono pero sin restricción de tiempo
    HTTP (Cloudflare corta a ~100s).
    """
    try:
        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "status": "running",
                "started_at": _now_iso(),
                "updated_at": _now_iso(),
                "progress.phase": "parsing",
            }},
        )

        filas, errores, _debug = _parsear_csv_newman(
            contenido, nif_titular, nombre_titular or "",
        )
        if not filas:
            raise RuntimeError(
                f"No se pudieron extraer filas válidas del CSV. Errores: {errores[:3]}"
            )

        periodo_norm_in = _norm_periodo(periodo) if periodo else ""
        for f in filas:
            if ejercicio and not f.get("ejercicio"):
                f["ejercicio"] = str(ejercicio)
            if periodo_norm_in and not f.get("periodo"):
                f["periodo"] = periodo_norm_in

        if ejercicio:
            filas = [f for f in filas if str(f.get("ejercicio", "")) == str(ejercicio)]
        if periodo:
            filas = [f for f in filas if _norm_periodo(f.get("periodo")) == periodo_norm_in]

        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "progress.total": len(filas),
                "progress.phase": "matching",
                "updated_at": _now_iso(),
            }},
        )

        num_series_csv = [f["num_serie_factura"] for f in filas]
        base_filtro_bd: dict = {"nif_titular": nif_titular}
        if ejercicio:
            base_filtro_bd["ejercicio"] = str(ejercicio)
        if periodo:
            periodo_norm = _norm_periodo(periodo)
            base_filtro_bd["periodo"] = {"$in": [
                periodo_norm,
                str(int(periodo_norm)) if periodo_norm.isdigit() else periodo_norm,
            ]}

        NS_CHUNK = 20_000
        existentes: set[str] = set()
        for i in range(0, len(num_series_csv), NS_CHUNK):
            chunk_ns = num_series_csv[i : i + NS_CHUNK]
            filtro_bd = dict(base_filtro_bd)
            filtro_bd["num_serie_factura"] = {"$in": chunk_ns}
            async for d in _db.facturas_sii.find(filtro_bd, {"_id": 0, "num_serie_factura": 1}):
                existentes.add(d["num_serie_factura"])

        faltantes = [f for f in filas if f["num_serie_factura"] not in existentes]
        total_faltantes = len(faltantes)

        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "progress.faltantes_total": total_faltantes,
                "progress.ya_en_bd": len(existentes),
                "progress.phase": "inserting",
                "updated_at": _now_iso(),
            }},
        )

        # Insert por lotes con actualización del progreso cada lote.
        batch_size = 2000
        insertadas = 0
        for i in range(0, total_faltantes, batch_size):
            chunk = faltantes[i : i + batch_size]
            await upsert_facturas_bulk(
                "facturas_sii", chunk, "conciliacion_newman",
            )
            insertadas += len(chunk)
            await _db.jobs.update_one(
                {"id": job_id},
                {"$set": {
                    "progress.processed": insertadas,
                    "updated_at": _now_iso(),
                }},
            )

        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "status": "done",
                "result": {
                    "filtro": {"nif_titular": nif_titular, "ejercicio": ejercicio, "periodo": periodo},
                    "total_csv": len(filas),
                    "ya_en_bd": len(existentes),
                    "insertadas": insertadas,
                    "errores_csv": errores[:50],
                },
                "finished_at": _now_iso(),
                "updated_at": _now_iso(),
                "progress.phase": "done",
            }},
        )
    except Exception as e:  # noqa: BLE001
        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "status": "error",
                "error_message": f"{type(e).__name__}: {e}",
                "finished_at": _now_iso(),
                "updated_at": _now_iso(),
            }},
        )


@router.post("/sii/conciliar-newman/importar-faltantes-async")
async def conciliar_newman_importar_async(
    file: UploadFile = File(...),
    nif_titular: str = Form(...),
    nombre_titular: str = Form(""),
    ejercicio: Optional[str] = Form(None),
    periodo: Optional[str] = Form(None),
):
    """Versión asíncrona de `/importar-faltantes`. Encola un job en background
    y devuelve un `job_id` que el cliente consulta con `GET /api/jobs/{id}`.

    Imprescindible para CSVs grandes (cientos de miles de filas) porque
    Cloudflare corta conexiones HTTP idle a ~100s.
    """
    contenido = await file.read()
    if not contenido:
        raise HTTPException(400, "El CSV está vacío")

    job_id = uuid.uuid4().hex
    job_doc = {
        "id": job_id,
        "type": "conciliar-newman-import",
        "status": "queued",
        "progress": {
            "processed": 0,
            "total": 0,
            "faltantes_total": 0,
            "ya_en_bd": 0,
            "phase": "queued",
        },
        "params": {
            "nif_titular": nif_titular,
            "nombre_titular": nombre_titular,
            "ejercicio": ejercicio,
            "periodo": periodo,
            "file_size_bytes": len(contenido),
        },
        "result": None,
        "error_message": None,
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "updated_at": _now_iso(),
    }
    await _db.jobs.insert_one(job_doc)
    asyncio.create_task(
        _ejecutar_importar_faltantes_job(
            job_id, contenido, nif_titular, nombre_titular, ejercicio, periodo,
        ),
    )
    return {"job_id": job_id, "status": "queued"}


@router.post("/sii/conciliar-newman/importar-lote")
async def conciliar_newman_importar_lote(payload: dict):
    """Variante ligera del importador: recibe un JSON con la lista de facturas
    YA filtradas y mapeadas por el frontend (a partir de `faltantes_completas`
    de `/sii/conciliar-newman`). Evita re-subir el CSV gigante.

    Body esperado:
    {
      "nif_titular": "A95000295",
      "nombre_titular": "MI EMPRESA SL",     // opcional
      "ejercicio": "2026", "periodo": "05",  // opcionales (sólo informativo)
      "facturas": [ { "num_serie_factura": "...", "base_imponible": ..., ... }, ... ]
    }

    Hace `upsert_facturas_bulk` con `fuente_ultima: "conciliacion_newman"`.
    Idempotente: si la factura ya existe, se actualiza in-place (no duplica).
    """
    nif_titular = (payload or {}).get("nif_titular", "").strip()
    if not nif_titular:
        raise HTTPException(400, "Falta `nif_titular`")
    nombre_titular = (payload or {}).get("nombre_titular", "").strip()
    facturas = (payload or {}).get("facturas") or []
    if not isinstance(facturas, list) or not facturas:
        raise HTTPException(400, "Falta `facturas` (lista no vacía)")

    # Asegura que cada doc tiene num_serie_factura y nif_titular coherente.
    docs: list[dict] = []
    for f in facturas:
        if not isinstance(f, dict):
            continue
        ns = (f.get("num_serie_factura") or "").strip()
        if not ns:
            continue
        doc = dict(f)
        doc["num_serie_factura"] = ns
        doc["nif_titular"] = nif_titular
        if nombre_titular:
            doc["nombre_titular"] = nombre_titular
        docs.append(doc)

    if not docs:
        raise HTTPException(400, "Ninguna factura del lote tiene num_serie_factura válido")

    # Inserción por chunks de 2000 (igual que el endpoint con CSV).
    batch_size = 2000
    insertadas = 0
    for i in range(0, len(docs), batch_size):
        chunk = docs[i : i + batch_size]
        await upsert_facturas_bulk("facturas_sii", chunk, "conciliacion_newman")
        insertadas += len(chunk)

    return {
        "filtro": {
            "nif_titular": nif_titular,
            "ejercicio": (payload or {}).get("ejercicio"),
            "periodo": (payload or {}).get("periodo"),
        },
        "recibidas": len(facturas),
        "insertadas": insertadas,
    }


@router.post("/facturas/sii/limpiar-tipo-impositivo-anomalo")
async def limpiar_tipo_impositivo_anomalo(
    dry_run: bool = True,
    _: dict = Depends(require_permission("conciliacion.import")),
) -> dict:
    """Limpia registros con `tipo_impositivo` fuera del rango legal [0, 30].

    Causa conocida: el export Newman/Postman a veces concatena
    TipoImpositivo + CuotaRepercutida en una celda (p.ej. "211.84" cuando
    debería ser tipo=21, cuota=1.84). Este endpoint repara la BD aplicando la
    heurística de `_sanear_tipo_y_cuota` a las facturas ya cargadas.

    - `dry_run=true` (default): cuenta cuántas se afectarían sin escribir.
    - `dry_run=false`: aplica los cambios y devuelve el contador real.

    Solo opera sobre `facturas_sii`. Las facturas comerciales no se tocan.
    """
    from pymongo import UpdateOne  # noqa: WPS433

    lo, hi = _TIPO_IMPOSITIVO_RANGO_VALIDO
    filtro = {
        "$or": [
            {"tipo_impositivo": {"$gt": hi}},
            {"tipo_impositivo": {"$lt": lo}},
        ]
    }
    total = await _db.facturas_sii.count_documents(filtro)
    if total == 0 or dry_run:
        return {
            "encontradas": total,
            "actualizadas": 0,
            "dry_run": dry_run,
            "mensaje": (
                "Dry run — pasa ?dry_run=false para aplicar"
                if dry_run else "Nada que limpiar"
            ),
        }

    reparadas = 0
    descartadas = 0
    cursor = _db.facturas_sii.find(
        filtro,
        {"_id": 1, "num_serie_factura": 1, "tipo_impositivo": 1, "cuota_repercutida": 1},
    )
    ops: list = []
    async for d in cursor:
        antes_tipo = d.get("tipo_impositivo")
        sane = {"tipo_impositivo": antes_tipo, "cuota_repercutida": d.get("cuota_repercutida")}
        _sanear_tipo_y_cuota(sane)
        if sane["tipo_impositivo"] != antes_tipo:
            if sane["tipo_impositivo"] is not None:
                reparadas += 1
            else:
                descartadas += 1
            ops.append(
                UpdateOne(
                    {"_id": d["_id"]},
                    {"$set": {
                        "tipo_impositivo": sane["tipo_impositivo"],
                        "cuota_repercutida": sane["cuota_repercutida"],
                    }},
                )
            )
            if len(ops) >= 1000:
                await _db.facturas_sii.bulk_write(ops, ordered=False)
                ops = []
    if ops:
        await _db.facturas_sii.bulk_write(ops, ordered=False)

    return {
        "encontradas": total,
        "actualizadas": reparadas + descartadas,
        "reparadas_con_cuota_extraida": reparadas,
        "descartadas_a_null": descartadas,
        "dry_run": False,
    }


@router.post("/facturas/sii/diagnosticar-newman-wrap")
async def diagnosticar_newman_wrap(
    aplicar: bool = False,
    _: dict = Depends(require_permission("conciliacion.import")),
) -> dict:
    """Diagnostica (y opcionalmente sanea) facturas afectadas por el bug de
    wrap del export de Newman (`scripts/extraer_csv.py` previo al fix de los
    bordes `|` ASCII).

    **Bug** (ya corregido en el script): cuando una fila CSVROW: rompía con
    wrap justo al final de una celda, el `|` delimitador de columna se
    eliminaba al limpiar bordes. Al reensamblar, dos columnas quedaban
    pegadas y los campos posteriores se desplazaban una posición a la derecha.
    El punto exacto del wrap varía por fila, así que reparar por shift-back
    no es fiable. Esta función se limita a:

    1. Detectar el residuo del bug (signatura: `num_registro_presentacion`
       termina en `'` — proviene en realidad de `TimestampPresentacion`).
    2. **Recuperar** `timestamp_presentacion` desde ahí (quitando la comilla).
    3. **Limpiar** campos claramente contaminados:
       - `num_registro_presentacion` → unset.
       - `contraparte_nombre`: si termina en un estado SII conocido
         (`Correcta`, `AceptadaConErrores`, etc.), se separa el sufijo y
         se asigna a `estado_factura`.
       - `estado_factura`: si es un string puramente numérico (proviene
         de `CSVAEAT` por shift), se mueve a `csv_aeat` y queda en None.
       - `tipo_impositivo` fuera de los tipos legales {21,10,7,5,4,0} en
         registros con esta signatura: unset (suele ser el cuota_repercutida).

    Tras esto, **re-importar el CSV** regenerado con el `extraer_csv.py`
    corregido repuebla todos los campos correctamente (los upserts hacen
    `$set` solo de valores no vacíos, por eso primero hay que limpiar los
    residuos).

    Modo:
      - `aplicar=false` (default): solo cuenta y devuelve muestra.
      - `aplicar=true`: aplica la limpieza.
    """
    from pymongo import UpdateOne  # noqa: WPS433

    filtro = {"num_registro_presentacion": {"$regex": r"'$"}}
    total = await _db.facturas_sii.count_documents(filtro)
    muestra = await _db.facturas_sii.find(
        filtro,
        {
            "_id": 0,
            "num_serie_factura": 1,
            "tipo_impositivo": 1,
            "cuota_repercutida": 1,
            "contraparte_nif": 1,
            "contraparte_nombre": 1,
            "estado_factura": 1,
            "num_registro_presentacion": 1,
        },
    ).limit(5).to_list(None)

    if not aplicar:
        return {
            "encontradas": total,
            "muestra": muestra,
            "aplicado": False,
            "mensaje": (
                "Modo diagnóstico. Para limpiar, pasa ?aplicar=true. "
                "Tras limpiar, re-importar el CSV regenerado con el "
                "extraer_csv.py corregido para repoblar los campos."
            ),
        }

    ESTADOS_SII = (
        "Correcta",
        "AceptadaConErrores",
        "AceptadaPorOtraUE",
        "Anulada",
        "NoRegistrada",
        "Rechazada",
    )
    TIPOS_LEGALES = {0.0, 4.0, 5.0, 7.0, 10.0, 21.0}

    reparadas = 0
    ops: list = []
    cursor = _db.facturas_sii.find(filtro)
    async for d in cursor:
        sets: dict = {}
        unsets: dict = {}

        # 1) Recupera timestamp_presentacion desde num_registro_presentacion.
        nrp = d.get("num_registro_presentacion")
        if isinstance(nrp, str) and nrp.endswith("'"):
            sets["timestamp_presentacion"] = nrp.rstrip("'").strip()
            unsets["num_registro_presentacion"] = ""

        # 2) Repara contraparte_nombre concatenado con estado SII al final.
        nombre = d.get("contraparte_nombre")
        if isinstance(nombre, str):
            for est in ESTADOS_SII:
                if nombre.endswith(est) and nombre != est:
                    sets["contraparte_nombre"] = nombre[: -len(est)].rstrip()
                    sets["estado_factura"] = est
                    break

        # 3) estado_factura puramente numérico → en realidad es CSVAEAT.
        estado = sets.get("estado_factura", d.get("estado_factura"))
        # Solo movemos a csv_aeat si NO estamos a punto de poner un estado
        # válido en el paso 2 (en cuyo caso el estado_factura actual era
        # el csv_aeat shifted).
        if "estado_factura" in sets:
            estado_previo = d.get("estado_factura")
            if isinstance(estado_previo, str) and estado_previo.isdigit():
                sets["csv_aeat"] = estado_previo
        elif isinstance(estado, str) and estado.isdigit():
            sets["csv_aeat"] = estado
            unsets["estado_factura"] = ""

        # 4) tipo_impositivo fuera de los tipos legales → unset
        #    (suele ser el cuota_repercutida shifted).
        tipo = d.get("tipo_impositivo")
        if isinstance(tipo, (int, float)) and float(tipo) not in TIPOS_LEGALES:
            unsets["tipo_impositivo"] = ""

        if sets or unsets:
            update: dict = {}
            if sets:
                update["$set"] = sets
            if unsets:
                update["$unset"] = unsets
            ops.append(UpdateOne({"_id": d["_id"]}, update))
            reparadas += 1
            if len(ops) >= 1000:
                await _db.facturas_sii.bulk_write(ops, ordered=False)
                ops = []
    if ops:
        await _db.facturas_sii.bulk_write(ops, ordered=False)

    return {
        "encontradas": total,
        "saneadas": reparadas,
        "aplicado": True,
        "mensaje": (
            f"Se limpiaron campos contaminados en {reparadas} facturas. "
            f"Próximo paso: re-importar el CSV regenerado con el "
            f"extraer_csv.py corregido para repoblar los campos correctos."
        ),
    }


@router.post("/facturas/sii/redondear-importes")
async def redondear_importes(
    dry_run: bool = True,
    _: dict = Depends(require_permission("conciliacion.import")),
) -> dict:
    """Aplica round(_, 2) a `base_imponible`, `cuota_repercutida`, `importe_total`
    en registros con errores de precisión float (p.ej. 3.86 que se guardó como
    3.8600000000000003 al sumar Sujeta.Exenta + Sujeta.NoExenta).

    Detecta registros donde `round(x,2) != x` para esos campos y los corrige.
    `dry_run=true` (default) cuenta sin escribir.
    """
    from pymongo import UpdateOne  # noqa: WPS433

    CAMPOS = ("base_imponible", "cuota_repercutida", "importe_total")
    cursor = _db.facturas_sii.find(
        {"$or": [{c: {"$ne": None}} for c in CAMPOS]},
        {"_id": 1, **{c: 1 for c in CAMPOS}},
    )
    afectadas = 0
    ops: list = []
    async for d in cursor:
        sets: dict = {}
        for c in CAMPOS:
            v = d.get(c)
            if v is None or not isinstance(v, (int, float)):
                continue
            r = round(float(v), 2)
            if r != v:
                sets[c] = r
        if sets:
            afectadas += 1
            if not dry_run:
                ops.append(UpdateOne({"_id": d["_id"]}, {"$set": sets}))
                if len(ops) >= 1000:
                    await _db.facturas_sii.bulk_write(ops, ordered=False)
                    ops = []
    if ops:
        await _db.facturas_sii.bulk_write(ops, ordered=False)
    return {
        "encontradas": afectadas,
        "actualizadas": 0 if dry_run else afectadas,
        "dry_run": dry_run,
        "mensaje": (
            "Dry run — pasa ?dry_run=false para aplicar"
            if dry_run else "Limpieza completada"
        ),
    }





def _consultar_mensual_real(
    client, nif_titular, nombre_titular, ejercicio, periodo, entorno,
    progress_cb=None, max_paginas=None, start_clave=None,
    start_pagina=0, start_invoices=0,
) -> tuple[list[dict], str, str]:
    """Invoca ConsultaLRFacturasEmitidas SIN IDFactura y mapea los registros
    devueltos al modelo canónico de Factura.

    **Paginación completa**: la AEAT devuelve hasta 10.000 registros por página
    y marca `IndicadorPaginacion=ConMasRegistros` cuando hay más. En ese caso
    re-invocamos con `ClavePaginacion` = último registro devuelto, hasta
    obtener todas las facturas del periodo.
    Devuelve (facturas, request_xml, response_xml) — los XML son los de la
    **última** página (los anteriores también quedan auditables en /logs).
    """
    # Reutilizamos la infra de zeep del cliente. Adaptamos el filtro: omitimos
    # IDFactura para que el SII devuelva todas las facturas del periodo.
    # Inline para no extender la API abstracta del SIIClient.
    from lxml import etree
    from requests import Session
    from zeep import Client, Settings
    from zeep.exceptions import XMLSyntaxError as ZeepXMLSyntaxError
    from zeep.plugins import HistoryPlugin
    from zeep.transports import Transport
    from sii_client import WSDL_LOCAL_FILE, _interpretar_html_aeat

    cert_path, key_path = client._extract_pem()
    history = HistoryPlugin()
    try:
        session = Session()
        session.cert = (cert_path, key_path)
        transport = Transport(session=session, timeout=30, operation_timeout=180)
        settings = Settings(strict=False, xml_huge_tree=True)
        z = Client(WSDL_LOCAL_FILE.as_uri(), transport=transport,
                   settings=settings, plugins=[history])
        binding = next(iter(z.wsdl.bindings.keys()))
        service = z.create_service(binding, ENDPOINTS[entorno])

        cabecera = {
            "IDVersionSii": "1.1",
            "Titular": {"NombreRazon": nombre_titular, "NIF": nif_titular},
        }
        filtro = {
            "PeriodoLiquidacion": {"Ejercicio": ejercicio, "Periodo": periodo},
        }
        out: list[dict] = []
        clave_pag = start_clave
        pagina = int(start_pagina or 0)

        # --- helper de retry para llamadas SOAP transitorias --------------
        def _is_transient_network_error(exc: BaseException) -> bool:
            """Detecta errores de red transitorios que merecen reintento.
            Recorremos la cadena de causas/contexts porque `zeep` envuelve
            la excepción original de `urllib3`/`requests`.
            """
            visited: set[int] = set()
            cur: Optional[BaseException] = exc
            while cur is not None and id(cur) not in visited:
                visited.add(id(cur))
                if isinstance(cur, (ConnectionResetError, ConnectionAbortedError,
                                    ConnectionRefusedError, BrokenPipeError,
                                    TimeoutError)):
                    return True
                # requests / urllib3
                name = type(cur).__name__
                if name in {
                    "ConnectionError", "ChunkedEncodingError", "ProtocolError",
                    "ReadTimeoutError", "ReadTimeout", "RemoteDisconnected",
                    "IncompleteRead",
                }:
                    return True
                cur = cur.__cause__ or cur.__context__
            return False

        def _llamar_sii_con_retry(filtro_actual: dict, n_pagina: int):
            """Invoca el SOAP con reintentos exponenciales ante errores de red
            transitorios (ConnectionResetError 10054 típicos de AEAT en
            descargas largas)."""
            delays = [2, 5, 10, 20, 30]  # 5 reintentos, máx ~67s espera total
            last_exc: Optional[Exception] = None
            for intento, sleep_s in enumerate([0, *delays]):
                if sleep_s:
                    _logger.warning(
                        "SII página %d: reintento %d tras %ds por error "
                        "de red transitorio (%s)",
                        n_pagina, intento, sleep_s,
                        type(last_exc).__name__ if last_exc else "?",
                    )
                    time.sleep(sleep_s)
                try:
                    return service.ConsultaLRFacturasEmitidas(
                        Cabecera=cabecera, FiltroConsulta=filtro_actual
                    )
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if not _is_transient_network_error(exc):
                        raise
            # Agotados los reintentos
            assert last_exc is not None
            raise last_exc
        # ------------------------------------------------------------------

        while True:
            pagina += 1
            if clave_pag is not None:
                filtro["ClavePaginacion"] = clave_pag
            try:
                resp = _llamar_sii_con_retry(filtro, pagina)
            except (ZeepXMLSyntaxError, Exception) as exc:  # noqa: BLE001
                raw = ""
                req_dump = ""
                if history.last_sent:
                    try:
                        req_dump = etree.tostring(
                            history.last_sent["envelope"], pretty_print=True
                        ).decode(errors="ignore")
                    except Exception:  # noqa: BLE001
                        req_dump = ""
                if history.last_received:
                    try:
                        raw = etree.tostring(
                            history.last_received["envelope"],
                            pretty_print=True,
                        ).decode(errors="ignore")
                    except Exception:  # noqa: BLE001
                        raw = ""
                hint = _interpretar_html_aeat(raw) if raw else ""
                detail = hint or f"{exc}"
                if raw:
                    detail += (
                        f"\n\n— Cuerpo devuelto (primeros 600 chars):\n"
                        f"{raw[:600]}"
                    )
                err = RuntimeError(detail)
                err.request_xml = req_dump  # type: ignore[attr-defined]
                err.response_xml = raw  # type: ignore[attr-defined]
                raise err from exc
            registros = (
                getattr(resp, "RegistroRespuestaConsultaLRFacturasEmitidas", None)
                or getattr(resp, "RegistroRespuestaConsultaLRFactEmitidas", None)
                or []
            )
            # Datos de las facturas añadidas en ESTA página (para commit
            # incremental por página, no al final).
            len_antes = len(out)
            for r in registros:
                idf = getattr(r, "IDFactura", None)
                # En la respuesta de la AEAT el elemento se llama DatosFacturaEmitida
                # (no DatosFactura). Mantenemos el fallback por si en algún WSDL viejo
                # vinieran con el nombre antiguo.
                df = (
                    getattr(r, "DatosFacturaEmitida", None)
                    or getattr(r, "DatosFactura", None)
                )
                contra = getattr(df, "Contraparte", None) if df else None
                base, cuota, tipo, detalle_iva = _extraer_iva_emitida(df)
                total = getattr(df, "ImporteTotal", None) if df is not None else None
                out.append(
                    {
                        "num_serie_factura": getattr(
                            idf, "NumSerieFacturaEmisor", None
                        ),
                        "fecha_expedicion": str(
                            getattr(idf, "FechaExpedicionFacturaEmisor", "")
                        )
                        or None,
                        "nif_emisor": nif_titular,
                        "nombre_emisor": nombre_titular,
                        "ejercicio": ejercicio,
                        "periodo": periodo,
                        "nif_titular": nif_titular,
                        "contraparte_nif": getattr(contra, "NIF", None)
                        if contra
                        else None,
                        "contraparte_nombre": getattr(contra, "NombreRazon", None)
                        if contra
                        else None,
                        "tipo_factura": getattr(df, "TipoFactura", None)
                        if df
                        else None,
                        "clave_regimen_especial": getattr(
                            df, "ClaveRegimenEspecialOTrascendencia", None
                        )
                        if df
                        else None,
                        "descripcion_operacion": getattr(
                            df, "DescripcionOperacion", None
                        )
                        if df
                        else None,
                        "fecha_operacion": str(
                            getattr(df, "FechaOperacion", "")
                        )
                        or None
                        if df
                        else None,
                        "base_imponible": float(base) if base is not None else None,
                        "tipo_impositivo": float(tipo) if tipo is not None else None,
                        "cuota_repercutida": float(cuota)
                        if cuota is not None
                        else None,
                        "importe_total": float(total)
                        if total is not None
                        else None,
                        "detalle_iva": detalle_iva,
                    }
                )
            # ---- Paginación AEAT --------------------------------------
            # IndicadorPaginacionType ∈ {"S", "N"} (sí hay más / no hay más).
            indic = getattr(resp, "IndicadorPaginacion", "N")
            if str(indic) != "S":
                break
            if not registros:
                break
            ultimo = registros[-1]
            uidf = getattr(ultimo, "IDFactura", None)
            if uidf is None:
                break
            num_last = getattr(uidf, "NumSerieFacturaEmisor", None)
            fecha_last = getattr(uidf, "FechaExpedicionFacturaEmisor", None)
            if not (num_last and fecha_last):
                break
            # Normaliza la fecha a DD-MM-YYYY que es el formato exigido por AEAT
            # (zeep puede devolver datetime.date o str según mapping XSD).
            try:
                from datetime import date as _date
                if isinstance(fecha_last, _date):
                    fecha_last = fecha_last.strftime("%d-%m-%Y")
                else:
                    fecha_last = str(fecha_last)
            except Exception:  # noqa: BLE001
                fecha_last = str(fecha_last)
            clave_pag = {
                "IDEmisorFactura": {"NIF": nif_titular},
                "NumSerieFacturaEmisor": num_last,
                "FechaExpedicionFacturaEmisor": fecha_last,
            }
            _logger.info(
                "SII consulta mensual: página %d completada, %d facturas "
                "acumuladas (total job: %d), siguiente desde %s/%s",
                pagina, len(out), int(start_invoices or 0) + len(out),
                num_last, fecha_last,
            )
            if progress_cb is not None:
                try:
                    facturas_pagina = out[len_antes:]
                    total_acumuladas = int(start_invoices or 0) + len(out)
                    if progress_cb(pagina, total_acumuladas, clave_pag, facturas_pagina):
                        _logger.info(
                            "Job cancelado por el usuario tras página %d",
                            pagina,
                        )
                        break
                except Exception:  # noqa: BLE001
                    _logger.exception("progress_cb falló")
            if max_paginas is not None and pagina >= max_paginas:
                _logger.info(
                    "Tope max_paginas=%d alcanzado, deteniendo paginación",
                    max_paginas,
                )
                break
        # XML crudos de la **última** página recibida (las anteriores ya
        # quedaron auditadas en el history.last_* mientras se acumulaban).
        last_req = ""
        last_resp = ""
        if history.last_sent:
            try:
                last_req = etree.tostring(
                    history.last_sent["envelope"], pretty_print=True
                ).decode()
            except Exception:  # noqa: BLE001
                pass
        if history.last_received:
            try:
                last_resp = etree.tostring(
                    history.last_received["envelope"], pretty_print=True
                ).decode()
            except Exception:  # noqa: BLE001
                pass
        return out, last_req, last_resp
    finally:
        import os as _os
        for p in (cert_path, key_path):
            try:
                _os.unlink(p)
            except OSError:
                pass


@router.get("/comercial/csv-template")
async def csv_template_comercial():
    csv_text = ";".join(CAMPOS_CANONICOS) + "\n"
    csv_text += "F2025-001;15-01-2025;A87654321;Proveedor SA;2025;01;B12345678;B11111111;Cliente Demo;F1;01;Servicios enero;15-01-2025;100,00;21,00;21,00;121,00\n"
    return StreamingResponse(
        io.BytesIO(csv_text.encode("utf-8-sig")),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=plantilla_comercial.csv"
        },
    )


@router.post("/comercial/csv")
async def upload_csv_comercial(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "Debe ser un archivo .csv o .txt")
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    # Detecta automáticamente el formato del report (SAP FI o SIGLO) por la
    # firma de cabeceras. Si no coincide ninguno, cae al parser CSV genérico.
    origen_detectado = _detectar_formato_tabular(text)
    if origen_detectado:
        catalogo = await _cargar_catalogo_sociedades()
        registros, errores = _parsear_report_tabular(
            text, origen_detectado, catalogo_sociedades=catalogo,
        )
    else:
        registros, errores = _parsear_csv_generico(text)

    total = 0
    for norm in registros:
        try:
            FacturaDatos(**norm)
        except Exception as e:  # noqa: BLE001
            errores.append({
                "fila": -1,
                "num_serie_factura": norm.get("num_serie_factura"),
                "motivo": str(e),
            })
            continue
        await upsert_factura("facturas_comercial", norm, "csv_comercial")
        total += 1

    # Tras importar, hacemos match con facturas_sii y devolvemos un mini
    # resumen para que el frontend muestre el resultado de la comparativa.
    nums = [r["num_serie_factura"] for r in registros if r.get("num_serie_factura")]
    matched_count = 0
    if nums:
        matched_count = await _db.facturas_sii.count_documents(
            {"num_serie_factura": {"$in": nums}}
        )
    return {
        "total": total,
        "errores": errores,
        "origen": origen_detectado,
        "matches_sii": matched_count,
        "sin_match_sii": max(0, len(nums) - matched_count),
    }


def _parsear_csv_generico(text: str) -> tuple[list[dict], list[dict]]:
    """Parser CSV "clásico" (con cabeceras estándar, separador autodetectado)."""
    sample = next((l for l in text.splitlines() if l.strip()), "")
    delim = max((";", ",", "\t", "|"), key=lambda c: sample.count(c))
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames or "num_serie_factura" not in {
        f.strip() for f in reader.fieldnames
    }:
        return [], [{
            "fila": 0,
            "motivo": "Cabeceras inválidas. La columna 'num_serie_factura' "
                      "es obligatoria. Descarga la plantilla con "
                      "/api/comercial/csv-template.",
        }]
    registros, errores = [], []
    for idx, row in enumerate(reader, start=1):
        norm = normalize_factura_row(row)
        if not norm.get("num_serie_factura"):
            errores.append({"fila": idx, "motivo": "num_serie_factura vacío"})
            continue
        registros.append(norm)
    return registros, errores


# ---------- SAP-style / SIGLO report parser --------------------------------

# Catálogo de formatos tabulares soportados. Cada formato define las firmas
# necesarias para reconocer su cabecera y la lista de alias por columna
# canónica. Si añades un origen nuevo, lo declaras aquí y el parser lo soporta
# automáticamente.
_FORMATOS_TABULARES: dict[str, dict] = {
    "SAP": {
        "header_signatures": (
            "Soc.", "Doc.causante", "Nº doc.oficial",
            "Tp.impos.", "BaseImpon", "Impto.ML",
        ),
        "col_num":  ["Nº doc.oficial"],
        "col_fexp": ["Fe.doc.or."],          # 1ª ocurrencia
        "col_fope": ["Fe.doc.or."],          # 2ª ocurrencia
        "col_tipo": ["Tp.impos."],
        "col_base": ["BaseImpon"],
        "col_imp":  ["Impto.ML"],
        "col_soc":  ["Soc."],
    },
    "SIGLO": {
        "header_signatures": (
            "Soc.", "Doc.caus.", "Nº oficial",
            "Tp.impos.", "BaseImpon", "Impto.ML",
        ),
        "col_num":  ["Nº oficial"],
        "col_fexp": ["Fe.doc.or."],          # 1ª ocurrencia
        "col_fope": ["Fe.doc.or."],          # 2ª ocurrencia
        "col_tipo": ["Tp.impos."],
        "col_base": ["BaseImpon"],
        "col_imp":  ["Impto.ML"],
        "col_soc":  ["Soc."],
    },
}


# ---------------------------------------------------------------------------
# Catálogo de Sociedades (Soc. → NIF titular + nombre)
# ---------------------------------------------------------------------------

# Default cableado para arranque limpio. El cliente puede sobreescribir/ampliar
# vía `PUT /api/admin/sociedades` (se persiste en `_db.sociedades_catalogo`).
_SOCIEDADES_DEFAULT: dict[str, dict] = {
    "4432": {"nif_titular": "A95000295", "nombre_titular": "TotalEnergies Clientes S.A.U."},
    "2239": {"nif_titular": "A74251836", "nombre_titular": "BASER"},
}


async def _cargar_catalogo_sociedades() -> dict[str, dict]:
    """Devuelve el catálogo {soc: {nif_titular, nombre_titular}} combinando el
    default cableado con los overrides persistidos en BD.

    El doc en BD tiene formato:
      `{_id: "default", entries: {"4432": {nif_titular, nombre_titular}, ...}}`
    """
    doc = await _db.sociedades_catalogo.find_one({"_id": "default"}) or {}
    persisted = doc.get("entries") or {}
    merged: dict[str, dict] = {}
    for soc, info in _SOCIEDADES_DEFAULT.items():
        merged[str(soc)] = dict(info)
    for soc, info in persisted.items():
        if isinstance(info, dict) and info.get("nif_titular"):
            merged[str(soc)] = {
                "nif_titular": str(info["nif_titular"]).strip().upper(),
                "nombre_titular": str(info.get("nombre_titular") or "").strip(),
            }
    return merged


def _detectar_formato_tabular(text: str) -> Optional[str]:
    """Recorre las primeras 100 líneas y devuelve el nombre del primer formato
    cuya firma de cabecera coincida (`SAP`, `SIGLO`...). None si ninguna."""
    head = text.splitlines()[:100]
    for nombre, spec in _FORMATOS_TABULARES.items():
        sigs = spec["header_signatures"]
        for line in head:
            if all(sig in line for sig in sigs):
                return nombre
    return None


def _parsear_numero_sap(valor: str) -> Optional[float]:
    """Parsea importes en formato español/SAP:
       - signo '-' al final  → negativo
       - si hay ',': '.' = miles, ',' = decimal  (`1.234,56` → 1234.56)
       - si NO hay ',': '.' = decimal estilo SAP (`10.000` → 10.0)"""
    if valor is None:
        return None
    s = str(valor).strip()
    if not s or s in ("-", "--"):
        return None
    neg = s.endswith("-")
    if neg:
        s = s[:-1].rstrip()
    s = s.replace(" ", "")
    try:
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def _parsear_fecha_sap(valor: str) -> Optional[str]:
    """`07.05.2026` → `07-05-2026`."""
    s = (valor or "").strip()
    if not s:
        return None
    if "." in s and len(s) == 10:
        return s.replace(".", "-")
    return s


def _parsear_report_tabular(
    text: str, origen: str,
    catalogo_sociedades: Optional[dict[str, dict]] = None,
) -> tuple[list[dict], list[dict]]:
    """Parsea un report tabular SAP-style (SAP FI o SIGLO).

    Ambos formatos comparten:
      - cabeceras de texto + filas delimitadas por `|`
      - números en formato español con `,` decimal y signo `-` al final
      - fechas `dd.mm.yyyy`
      - múltiples filas por factura (una por tramo de IVA) → se agrupan por
        `num_serie_factura` sumando base y cuota, acumulando `detalle_iva`.

    Difiere sólo en los nombres de las columnas, definidos en
    `_FORMATOS_TABULARES[origen]`.

    `catalogo_sociedades` mapea `Soc.` (string sin padding) → `{nif_titular,
    nombre_titular}`. Si una `Soc.` no está en el catálogo, el registro queda
    sin `nif_titular` (se carga pero no aparece en filtros de sociedad).
    """
    spec = _FORMATOS_TABULARES[origen]
    lines = text.splitlines()
    header_idx = None
    sigs = spec["header_signatures"]
    for i, line in enumerate(lines):
        if all(sig in line for sig in sigs):
            header_idx = i
            break
    if header_idx is None:
        return [], [{
            "fila": 0,
            "motivo": f"Cabecera {origen} no encontrada",
        }]

    header_cells = [c.strip() for c in lines[header_idx].strip("|").split("|")]

    def _idx(aliases: list[str], occ: int = 0) -> Optional[int]:
        """Devuelve el índice de la ``occ``-ésima ocurrencia de cualquier
        alias en `header_cells`."""
        seen = 0
        for i, c in enumerate(header_cells):
            if c in aliases:
                if seen == occ:
                    return i
                seen += 1
        return None

    idx_num  = _idx(spec["col_num"])
    idx_fexp = _idx(spec["col_fexp"], 0)
    idx_fope = _idx(spec["col_fope"], 1)
    idx_tipo = _idx(spec["col_tipo"])
    idx_base = _idx(spec["col_base"])
    idx_imp  = _idx(spec["col_imp"])
    idx_soc  = _idx(spec.get("col_soc") or [], 0)

    faltan = [n for n, v in [
        ("Nº (oficial)", idx_num), ("Tp.impos.", idx_tipo),
        ("BaseImpon", idx_base), ("Impto.ML", idx_imp),
    ] if v is None]
    if faltan:
        return [], [{
            "fila": header_idx,
            "motivo": f"Columnas requeridas no encontradas ({origen}): "
                      f"{', '.join(faltan)}",
        }]

    catalogo = catalogo_sociedades or {}
    registros_por_num: dict[str, dict] = {}
    soc_no_mapeadas: set[str] = set()
    errores: list[dict] = []
    for i, line in enumerate(lines[header_idx + 1 :], start=header_idx + 2):
        s = line.rstrip()
        if not s.startswith("|"):
            continue
        # Líneas separadoras estilo `|------...|`
        if set(s) <= {"|", "-", " "}:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < len(header_cells):
            continue
        num = cells[idx_num] if idx_num < len(cells) else ""
        if not num:
            continue
        try:
            fexp = _parsear_fecha_sap(cells[idx_fexp]) if idx_fexp is not None else None
            fope = _parsear_fecha_sap(cells[idx_fope]) if idx_fope is not None else None
            tipo = _parsear_numero_sap(cells[idx_tipo])
            base = _parsear_numero_sap(cells[idx_base])
            cuota = _parsear_numero_sap(cells[idx_imp])
            soc = (
                cells[idx_soc].strip()
                if idx_soc is not None and idx_soc < len(cells)
                else ""
            )
            mapping = catalogo.get(soc) if soc else None
            if soc and not mapping:
                soc_no_mapeadas.add(soc)
            ejercicio = fexp.split("-")[-1] if fexp else None
            periodo = fexp.split("-")[1] if fexp else None
            # Una misma factura puede aparecer en varias filas (una por tramo
            # de IVA). Agrupamos por num_serie_factura sumando bases/cuotas y
            # acumulando los detalles para `detalle_iva`.
            agg = registros_por_num.get(num)
            if agg is None:
                agg = {
                    "num_serie_factura": num,
                    "fecha_expedicion": fexp,
                    "fecha_operacion": fope,
                    "ejercicio": ejercicio,
                    "periodo": periodo,
                    "base_imponible": 0.0,
                    "cuota_repercutida": 0.0,
                    "tipo_impositivo": None,
                    "detalle_iva": [],
                    "origen_comercial": origen,
                    "soc_origen": soc or None,
                    "nif_titular": (mapping or {}).get("nif_titular"),
                    "nombre_titular": (mapping or {}).get("nombre_titular"),
                }
                registros_por_num[num] = agg
            if base is not None:
                agg["base_imponible"] += base
            if cuota is not None:
                agg["cuota_repercutida"] += cuota
            agg["detalle_iva"].append({
                "tipo_impositivo": tipo,
                "base_imponible": base,
                "cuota_repercutida": cuota,
                "origen": origen,
            })
        except Exception as e:  # noqa: BLE001
            errores.append({"fila": i, "motivo": str(e), "raw": s[:200]})

    if soc_no_mapeadas:
        errores.append({
            "fila": -1,
            "motivo": (
                f"Sociedades en CSV no encontradas en el catálogo: "
                f"{sorted(soc_no_mapeadas)}. Los registros se cargan pero sin "
                f"nif_titular asignado. Actualiza el catálogo en "
                f"/api/admin/sociedades."
            ),
        })

    # Para el `tipo_impositivo` agregado: si la factura tiene una sola línea
    # de IVA, usamos su tipo; si tiene varios tramos lo dejamos a None para
    # evitar falsos positivos en la comparativa (la información detallada
    # queda en `detalle_iva`).
    for agg in registros_por_num.values():
        if len(agg["detalle_iva"]) == 1:
            agg["tipo_impositivo"] = agg["detalle_iva"][0]["tipo_impositivo"]
        # Redondeo defensivo de las sumas a 2 decimales (evita ruido de coma
        # flotante en agregados de 3+ líneas).
        agg["base_imponible"] = round(agg["base_imponible"], 2)
        agg["cuota_repercutida"] = round(agg["cuota_repercutida"], 2)
    return list(registros_por_num.values()), errores


# --- Alias retrocompatibles (no romper imports externos) -------------------
_SAP_HEADER_SIG = _FORMATOS_TABULARES["SAP"]["header_signatures"]


def _detectar_sap_report(text: str) -> bool:
    """Mantiene la API previa: True si el report es SAP FI (no SIGLO)."""
    return _detectar_formato_tabular(text) == "SAP"


def _parsear_sap_report(text: str) -> tuple[list[dict], list[dict]]:
    """Mantiene la API previa: parsea como SAP FI."""
    return _parsear_report_tabular(text, "SAP")


@router.get("/facturas/{fuente}")
async def listar_facturas(
    fuente: str,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
):
    """fuente: 'sii' o 'comercial'."""
    if fuente not in ("sii", "comercial"):
        raise HTTPException(404, "Fuente no válida (sii|comercial)")
    coleccion = f"facturas_{fuente}"
    filtro = {}
    if search:
        filtro["num_serie_factura"] = {"$regex": search, "$options": "i"}
    total = await _db[coleccion].count_documents(filtro)
    cursor = (
        _db[coleccion]
        .find(filtro, {"_id": 0, "versiones": 0})
        .sort("ultima_actualizacion", -1)
        .skip(skip)
        .limit(limit)
    )
    items = await cursor.to_list(length=limit)
    return {"total": total, "items": items}


@router.get("/facturas/{fuente}/{num_serie}")
async def detalle_factura(fuente: str, num_serie: str):
    if fuente not in ("sii", "comercial"):
        raise HTTPException(404, "Fuente no válida")
    doc = await _db[f"facturas_{fuente}"].find_one(
        {"num_serie_factura": num_serie}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Factura no encontrada")
    return doc


def _aplicar_exclusion_tipo_iva_cero(com: dict, config: Optional[dict]) -> dict:
    """Si el flag `excluir_comercial_tipo_iva_cero` está activo en config y el
    doc comercial tiene `detalle_iva`, filtra las líneas con tipo_impositivo
    vacío o cero y recalcula `base_imponible` / `cuota_repercutida` a nivel
    cabecera con la suma del detalle filtrado.

    Devuelve un dict NUEVO (no muta el original).
    """
    if not com or not (config or {}).get("excluir_comercial_tipo_iva_cero", True):
        return com
    det = com.get("detalle_iva")
    if not isinstance(det, list) or not det:
        return com
    def _tipo_no_cero(linea):
        t = linea.get("tipo_impositivo")
        if t is None:
            return False
        try:
            return float(t) != 0
        except (TypeError, ValueError):
            return True
    det_filtrado = [l for l in det if _tipo_no_cero(l)]
    if len(det_filtrado) == len(det):
        return com

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

    return {
        **com,
        "detalle_iva": det_filtrado,
        "base_imponible": _sum_field(det_filtrado, "base_imponible"),
        "cuota_repercutida": _sum_field(det_filtrado, "cuota_repercutida"),
    }


def _build_row_from_docs(
    sii: Optional[dict],
    com: Optional[dict],
    ns: str,
    config: Optional[dict] = None,
) -> dict:
    com = _aplicar_exclusion_tipo_iva_cero(com, config)
    if sii and com:
        d = diff_facturas(sii, com, config)
        return {
            "num_serie_factura": ns,
            "estado": "coincide" if not d else "discrepancia",
            "en_sii": True, "en_comercial": True,
            "diferencias": d, "sii": sii, "comercial": com,
        }
    if sii:
        return {
            "num_serie_factura": ns,
            "estado": "solo_sii",
            "en_sii": True, "en_comercial": False,
            "diferencias": {}, "sii": sii, "comercial": None,
        }
    return {
        "num_serie_factura": ns,
        "estado": "solo_comercial",
        "en_sii": False, "en_comercial": True,
        "diferencias": {}, "sii": None, "comercial": com,
    }


async def _build_filtros(
    ejercicio: Optional[str],
    periodo: Optional[str],
    num_serie: Optional[str],
    excluir_base_cero: bool = False,
    nif_titular: Optional[str] = None,
) -> tuple[dict, dict]:
    """Construye filtros Mongo para SII y comercial a partir de los parámetros
    de consulta. No aplica restricciones implícitas: el universo SII se acota
    SÓLO si el usuario filtra explícitamente por ejercicio/periodo.

    Si `excluir_base_cero=True`, excluye en el filtro comercial las facturas
    con `base_imponible == 0` (o `null`). Útil para descartar anulaciones y
    asientos contables que no aportan a la conciliación.

    Si `nif_titular` viene informado, restringe ambos universos a esa sociedad.
    Para `facturas_comercial` se aceptan también docs sin `nif_titular`
    (cargados antes de añadir el campo) — así no se pierde data legacy hasta
    que el usuario haga backfill explícito vía la subida comercial.
    """
    import re

    filtro_sii: dict = {}
    filtro_com: dict = {}
    if ejercicio:
        filtro_sii["ejercicio"] = str(ejercicio)
        filtro_com["ejercicio"] = str(ejercicio)
    if periodo:
        # Acepta valor único ("01") o lista separada por comas ("01,02,03").
        # Multi: aplica $in en ambas colecciones.
        periodos_list = [
            p.strip() for p in str(periodo).split(",") if p.strip()
        ]
        if len(periodos_list) == 1:
            filtro_sii["periodo"] = periodos_list[0]
            filtro_com["periodo"] = periodos_list[0]
        elif len(periodos_list) > 1:
            filtro_sii["periodo"] = {"$in": periodos_list}
            filtro_com["periodo"] = {"$in": periodos_list}
    if num_serie:
        regex_ns = {"$regex": re.escape(num_serie), "$options": "i"}
        filtro_sii["num_serie_factura"] = regex_ns
        filtro_com["num_serie_factura"] = regex_ns
    if nif_titular:
        nif_norm = str(nif_titular).strip().upper()
        filtro_sii["nif_titular"] = nif_norm
        # `null` en $in matchea también docs sin el campo (legacy compat).
        filtro_com["nif_titular"] = {"$in": [nif_norm, None]}
    if excluir_base_cero:
        # `$ne: 0` excluye exactamente 0; ausencia o null se mantienen porque
        # el comercial ya almacena `base_imponible` como número tras el parser.
        filtro_com["base_imponible"] = {"$nin": [0, 0.0, None]}

    return filtro_sii, filtro_com


async def _comparativa_data(
    ejercicio: Optional[str],
    periodo: Optional[str],
    only_diffs: bool,
    num_serie: Optional[str] = None,
    estado: Optional[str] = None,
    nif_titular: Optional[str] = None,
) -> list[dict]:
    """Versión legacy (carga todo en memoria). Mantenida para `/export` y
    en escenarios sin necesidad de paginación a nivel BD.
    Para listado paginado usar `comparativa()` directamente, que ya optimiza
    los estados que no requieren cargar todo el SII."""
    config = await _load_comparativa_config()
    filtro_sii, filtro_com = await _build_filtros(
        ejercicio, periodo, num_serie,
        excluir_base_cero=config["excluir_comercial_base_cero"],
        nif_titular=nif_titular,
    )

    sii_docs = await _db.facturas_sii.find(
        filtro_sii, {"_id": 0, "versiones": 0}
    ).to_list(length=None)
    com_docs = await _db.facturas_comercial.find(
        filtro_com, {"_id": 0, "versiones": 0}
    ).to_list(length=None)

    sii_map = {d["num_serie_factura"]: d for d in sii_docs}
    com_map = {d["num_serie_factura"]: d for d in com_docs}
    todas = sorted(set(sii_map.keys()) | set(com_map.keys()))

    resultados = [
        _build_row_from_docs(sii_map.get(ns), com_map.get(ns), ns, config)
        for ns in todas
    ]

    if only_diffs:
        resultados = [r for r in resultados if r["estado"] != "coincide"]
    if estado:
        resultados = [r for r in resultados if r["estado"] == estado]
    return resultados


@router.get("/comparativa")
async def comparativa(
    skip: int = 0,
    limit: int = 50,
    only_diffs: bool = True,
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    num_serie: Optional[str] = None,
    estado: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    nif_titular: Optional[str] = None,
):
    """Compara facturas SII vs Comercial por `num_serie_factura`.

    Filtros: `ejercicio`, `periodo`, `num_serie` (contiene), `estado`
    (coincide | discrepancia | solo_sii | solo_comercial), `nif_titular`.
    Paginación: `skip` / `limit` (default 50).

    Optimización: para evitar cargar millones de facturas SII en memoria,
    construimos los resultados desde el universo comercial (que siempre es
    pequeño) y sólo cargamos SII docs cuyo `num_serie` aparece en comercial.
    El estado `solo_sii` requiere escanear SII fuera del comercial y se
    pagina a nivel BD para no consumir memoria.
    """
    config = await _load_comparativa_config()
    filtro_sii, filtro_com = await _build_filtros(
        ejercicio, periodo, num_serie,
        excluir_base_cero=config["excluir_comercial_base_cero"],
        nif_titular=nif_titular,
    )

    # 1) Universo comercial completo en scope (siempre pequeño)
    com_docs = await _db.facturas_comercial.find(
        filtro_com, {"_id": 0, "versiones": 0}
    ).to_list(length=None)
    com_map = {d["num_serie_factura"]: d for d in com_docs}
    com_keys = list(com_map.keys())

    # 2) Matches SII por num_serie ∈ comercial (uses unique index)
    sii_match_docs = await _db.facturas_sii.find(
        {**filtro_sii, "num_serie_factura": {"$in": com_keys}}
        if com_keys else {**filtro_sii, "num_serie_factura": {"$in": []}},
        {"_id": 0, "versiones": 0},
    ).to_list(length=None) if com_keys else []
    sii_match_map = {d["num_serie_factura"]: d for d in sii_match_docs}

    # 3) Filas de comercial: cada una será coincide / discrepancia / solo_comercial
    filas_com: list[dict] = []
    for ns, com in com_map.items():
        sii = sii_match_map.get(ns)
        filas_com.append(_build_row_from_docs(sii, com, ns, config))

    # 4) Contar SII fuera del comercial → estado solo_sii
    #    OJO: hay que preservar otros operadores que ya tenga filtro_sii sobre
    #    `num_serie_factura` (p.ej. el $regex de búsqueda del usuario). Mongo
    #    permite combinar $regex + $nin en el mismo subdocumento.
    solo_sii_filter = dict(filtro_sii)
    ns_clause = dict(solo_sii_filter.get("num_serie_factura") or {})
    ns_clause["$nin"] = com_keys
    solo_sii_filter["num_serie_factura"] = ns_clause
    solo_sii_total = await _db.facturas_sii.count_documents(solo_sii_filter)

    # 5) Aplicar filtros only_diffs / estado para decidir total e items
    # Mapeo de claves de ordenación del cliente a:
    # 1) Una función `key(row)` para ordenar filas ya construidas en memoria.
    # 2) Un campo Mongo para el cursor BD (solo necesario en estado='solo_sii').
    def _sort_key_row(key: str):
        if key == "num_serie_factura":
            return lambda r: r.get("num_serie_factura") or ""
        if key == "estado":
            return lambda r: r.get("estado") or ""
        if key == "fecha_expedicion":
            # Convierte 'DD-MM-YYYY' a entero comparable; nulos al final.
            def _f(r):
                s = (r.get("sii") or {}).get("fecha_expedicion") or (
                    r.get("comercial") or {}
                ).get("fecha_expedicion")
                if not isinstance(s, str):
                    return -1
                try:
                    d, m, y = s.split("-")
                    return int(y) * 10000 + int(m) * 100 + int(d)
                except (ValueError, AttributeError):
                    return -1
            return _f
        if key == "importe_sii":
            return lambda r: (r.get("sii") or {}).get("importe_total") or 0
        if key == "importe_comercial":
            return lambda r: (r.get("comercial") or {}).get("importe_total") or 0
        return lambda r: r.get("num_serie_factura") or ""

    SORT_DB_FIELD = {
        "num_serie_factura": "num_serie_factura",
        "fecha_expedicion": "fecha_expedicion",
        "importe_sii": "importe_total",
    }

    if estado == "solo_sii":
        # Sólo SII: paginamos a nivel BD. No mezclamos con filas_com.
        db_field = SORT_DB_FIELD.get(sort_by or "", "num_serie_factura")
        # En BD las fechas son strings 'DD-MM-YYYY' que NO ordenan
        # cronológicamente como strings; pero como TODAS las del año actual
        # comparten el sufijo '-YYYY', el orden lexicográfico coincide con el
        # cronológico dentro del mismo año. Para multi-año cargamos en memoria.
        direction = -1 if sort_dir == "desc" else 1
        cursor = _db.facturas_sii.find(
            solo_sii_filter, {"_id": 0, "versiones": 0}
        ).sort(db_field, direction).skip(skip).limit(limit)
        sii_pagina = await cursor.to_list(length=limit)
        items = [
            _build_row_from_docs(d, None, d["num_serie_factura"], config)
            for d in sii_pagina
        ]
        total = solo_sii_total
    else:
        # coincide / discrepancia / solo_comercial / all / diffs → desde filas_com
        if estado:
            filas = [r for r in filas_com if r["estado"] == estado]
        elif only_diffs:
            filas = [r for r in filas_com if r["estado"] != "coincide"]
        else:
            # "Todas" mezcla filas comerciales + solo_sii paginado
            filas = list(filas_com)

        sort_func = _sort_key_row(sort_by or "num_serie_factura")
        filas.sort(key=sort_func, reverse=(sort_dir == "desc"))
        # Cuando estado=None y only_diffs=False, sumamos contador de solo_sii al total
        # pero NO inyectamos los docs (sería caro). Si el usuario quiere verlos,
        # debe seleccionar explícitamente "Sólo en SII".
        if estado is None and not only_diffs:
            total = len(filas) + solo_sii_total
        else:
            total = len(filas)
        items = filas[skip : skip + limit]

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "campos_canonicos": CAMPOS_CANONICOS,
        "campos_numericos": CAMPOS_NUMERICOS,
        "items": items,
    }


@router.get("/comparativa/config")
async def get_comparativa_config():
    """Devuelve la configuración actual de comparativa, junto con los
    catálogos disponibles para que la UI pueda renderizar los selectores."""
    cfg = await _load_comparativa_config()
    # Detecta orígenes que existen en BD para mostrar toggles dinámicos
    origenes_db = await _db.facturas_comercial.aggregate([
        {"$group": {"_id": {"$ifNull": ["$origen_comercial", "desconocido"]}}},
    ]).to_list(length=None)
    origenes = sorted({o["_id"] for o in origenes_db}) or ["SAP", "SIGLO"]
    return {
        "campos_comparados": cfg["campos_comparados"],
        "invertir_signo_por_origen": cfg["invertir_signo_por_origen"],
        "excluir_comercial_base_cero": cfg["excluir_comercial_base_cero"],
        "excluir_comercial_tipo_iva_cero": cfg["excluir_comercial_tipo_iva_cero"],
        "campos_disponibles": list(CAMPOS_CANONICOS),
        "campos_numericos": list(CAMPOS_NUMERICOS),
        "origenes_disponibles": origenes,
        "campos_comparados_default": list(CAMPOS_COMPARADOS_DEFAULT),
    }


@router.get("/comparativa/totales")
async def comparativa_totales(
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    num_serie: Optional[str] = None,
    nif_titular: Optional[str] = None,
):
    """Devuelve totales agregados de Base Imponible y Cuota de IVA para:
      - SII (universo completo del filtro).
      - Comercial desglosado por `origen_comercial` (típicamente SAP, SIGLO).
      - Comercial agregado (Σ de todos los orígenes).
      - Diferencia SII − Σ Comercial y % de conciliación.

    Aplica la inversión de signo configurada en `comparativa_config` para los
    orígenes que la tienen activada (por defecto SAP, ya que en SAP FI las
    facturas emitidas se contabilizan con signo negativo).

    Los totales **ignoran** el filtro `only_diffs` (siempre se calculan sobre
    todas las facturas del universo filtrado por ejercicio/periodo/num_serie),
    porque el objetivo es comparar masas fiscales completas.

    Para SII usa el desglose `detalle_iva` cuando existe (sumando los tramos);
    si no, cae al top-level `base_imponible` / `cuota_repercutida`.
    """
    config = await _load_comparativa_config()
    filtro_sii, filtro_com = await _build_filtros(
        ejercicio, periodo, num_serie,
        excluir_base_cero=config["excluir_comercial_base_cero"],
        nif_titular=nif_titular,
    )

    excluir_tipo_iva_cero = config.get("excluir_comercial_tipo_iva_cero", True)

    def _linea_excluida_comercial(linea: dict) -> bool:
        """Devuelve True si una línea del detalle_iva del comercial debe
        excluirse del cómputo (tipo_impositivo vacío o cero).
        """
        if not excluir_tipo_iva_cero:
            return False
        t = linea.get("tipo_impositivo")
        if t is None:
            return True
        try:
            return float(t) == 0
        except (TypeError, ValueError):
            return False

    def _sum_doc_sii(doc: dict) -> tuple[float, float]:
        """Devuelve (base, cuota) de un doc SII (no aplica filtro)."""
        det = doc.get("detalle_iva")
        if isinstance(det, list) and det:
            base = sum(float(t.get("base_imponible") or 0) for t in det)
            cuota = sum(float(t.get("cuota_repercutida") or 0) for t in det)
            return base, cuota
        base = float(doc.get("base_imponible") or 0)
        cuota = float(doc.get("cuota_repercutida") or 0)
        return base, cuota

    def _sum_doc_comercial(doc: dict) -> tuple[float, float]:
        """Devuelve (base, cuota) de un doc COMERCIAL aplicando el filtro de
        líneas con tipo_impositivo vacío o cero (si está activo en config).

        Cuando no hay detalle_iva pero `excluir_tipo_iva_cero=True` y el
        `tipo_impositivo` a nivel cabecera es vacío/cero, el doc completo
        contribuye 0 (consistente con la idea de no comparar esas filas).
        """
        det = doc.get("detalle_iva")
        if isinstance(det, list) and det:
            base = sum(
                float(t.get("base_imponible") or 0)
                for t in det
                if not _linea_excluida_comercial(t)
            )
            cuota = sum(
                float(t.get("cuota_repercutida") or 0)
                for t in det
                if not _linea_excluida_comercial(t)
            )
            return base, cuota
        # Sin detalle_iva: aplicamos el filtro al doc-cabecera
        if _linea_excluida_comercial(doc):
            return 0.0, 0.0
        base = float(doc.get("base_imponible") or 0)
        cuota = float(doc.get("cuota_repercutida") or 0)
        return base, cuota

    def _fecha_ord(fecha_str: str | None) -> tuple[int, int, int] | None:
        """Convierte `DD-MM-YYYY` → tupla ordenable (YYYY, MM, DD)."""
        if not isinstance(fecha_str, str):
            return None
        try:
            d, m, y = fecha_str.split("-")
            return (int(y), int(m), int(d))
        except (ValueError, AttributeError):
            return None

    # --- SII ---
    sii_base = 0.0
    sii_cuota = 0.0
    sii_n = 0
    sii_ultima_fecha: str | None = None
    sii_ultima_orden: tuple[int, int, int] | None = None
    cursor = _db.facturas_sii.find(
        filtro_sii,
        {
            "_id": 0,
            "base_imponible": 1,
            "cuota_repercutida": 1,
            "detalle_iva": 1,
            "fecha_expedicion": 1,
        },
    )
    async for d in cursor:
        b, c = _sum_doc_sii(d)
        sii_base += b
        sii_cuota += c
        sii_n += 1
        ordn = _fecha_ord(d.get("fecha_expedicion"))
        if ordn and (sii_ultima_orden is None or ordn > sii_ultima_orden):
            sii_ultima_orden = ordn
            sii_ultima_fecha = d.get("fecha_expedicion")

    # --- Comercial por origen ---
    inv_map = config.get("invertir_signo_por_origen") or {}
    por_origen: dict[str, dict] = {}
    cursor = _db.facturas_comercial.find(
        filtro_com,
        {
            "_id": 0,
            "base_imponible": 1,
            "cuota_repercutida": 1,
            "tipo_impositivo": 1,
            "detalle_iva": 1,
            "origen_comercial": 1,
            "fecha_expedicion": 1,
        },
    )
    async for d in cursor:
        origen = d.get("origen_comercial") or "DESCONOCIDO"
        b, c = _sum_doc_comercial(d)
        if inv_map.get(origen):
            b, c = -b, -c
        bucket = por_origen.setdefault(
            origen,
            {
                "base": 0.0,
                "cuota": 0.0,
                "n_facturas": 0,
                "invertido": bool(inv_map.get(origen)),
                "ultima_fecha_expedicion": None,
                "_orden": None,
            },
        )
        bucket["base"] += b
        bucket["cuota"] += c
        bucket["n_facturas"] += 1
        ordn = _fecha_ord(d.get("fecha_expedicion"))
        if ordn and (bucket["_orden"] is None or ordn > bucket["_orden"]):
            bucket["_orden"] = ordn
            bucket["ultima_fecha_expedicion"] = d.get("fecha_expedicion")

    # --- Comercial total (Σ orígenes, ya con inversión aplicada) ---
    com_base = sum(o["base"] for o in por_origen.values())
    com_cuota = sum(o["cuota"] for o in por_origen.values())
    com_n = sum(o["n_facturas"] for o in por_origen.values())

    # --- Diferencias y % conciliado ---
    diff_base = round(sii_base - com_base, 2)
    diff_cuota = round(sii_cuota - com_cuota, 2)

    def _pct(num: float, denom: float) -> float | None:
        if denom == 0:
            return None
        return round(1.0 - abs(num) / abs(denom), 6)

    return {
        "sii": {
            "base": round(sii_base, 2),
            "cuota": round(sii_cuota, 2),
            "n_facturas": sii_n,
            "ultima_fecha_expedicion": sii_ultima_fecha,
        },
        "comercial_por_origen": {
            k: {
                "base": round(v["base"], 2),
                "cuota": round(v["cuota"], 2),
                "n_facturas": v["n_facturas"],
                "invertido": v["invertido"],
                "ultima_fecha_expedicion": v["ultima_fecha_expedicion"],
            }
            for k, v in sorted(por_origen.items())
        },
        "comercial_total": {
            "base": round(com_base, 2),
            "cuota": round(com_cuota, 2),
            "n_facturas": com_n,
        },
        "diferencias": {
            "base": diff_base,
            "cuota": diff_cuota,
            "pct_conciliado_base": _pct(diff_base, sii_base),
            "pct_conciliado_cuota": _pct(diff_cuota, sii_cuota),
        },
        "filtros": {
            "ejercicio": ejercicio,
            "periodo": periodo,
            "num_serie": num_serie,
            "nif_titular": nif_titular,
        },
    }


@router.put("/comparativa/config")
async def put_comparativa_config(payload: dict):
    """Actualiza la configuración de comparativa. Acepta:
      - `campos_comparados`: lista de campos canónicos a incluir en el diff.
      - `invertir_signo_por_origen`: dict `{ "SAP": bool, "SIGLO": bool, ... }`.
      - `excluir_comercial_base_cero`: bool — si True, ignora en la comparativa
        las filas comerciales con base imponible = 0 (típicamente facturas
        anuladas o ajustes contables que no aportan a la conciliación).
    """
    campos_in = payload.get("campos_comparados")
    if not isinstance(campos_in, list):
        raise HTTPException(400, "campos_comparados debe ser una lista")
    # Filtrar a campos canónicos válidos para evitar inyección de claves raras
    campos_valid = [c for c in campos_in if c in CAMPOS_CANONICOS]

    inv_in = payload.get("invertir_signo_por_origen") or {}
    if not isinstance(inv_in, dict):
        raise HTTPException(
            400, "invertir_signo_por_origen debe ser un dict { origen: bool }"
        )
    inv_clean = {str(k): bool(v) for k, v in inv_in.items() if k}

    excl_base_cero = bool(payload.get("excluir_comercial_base_cero", False))
    excl_tipo_iva_cero = bool(payload.get("excluir_comercial_tipo_iva_cero", True))

    await _db.comparativa_config.update_one(
        {"_id": _CONFIG_DOC_ID},
        {"$set": {
            "campos_comparados": campos_valid,
            "invertir_signo_por_origen": inv_clean,
            "excluir_comercial_base_cero": excl_base_cero,
            "excluir_comercial_tipo_iva_cero": excl_tipo_iva_cero,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {
        "ok": True,
        "campos_comparados": campos_valid,
        "invertir_signo_por_origen": inv_clean,
        "excluir_comercial_base_cero": excl_base_cero,
        "excluir_comercial_tipo_iva_cero": excl_tipo_iva_cero,
    }


@router.get("/comparativa/periodos")
async def comparativa_periodos(nif_titular: Optional[str] = None):
    """Devuelve los ejercicios/periodos distintos disponibles en `facturas_sii`
    (combinados con los de `facturas_comercial`) para poblar los filtros.

    Si se aporta `nif_titular`, sólo considera facturas de esa sociedad.
    En `facturas_comercial` se aceptan también docs sin `nif_titular` para
    no perder periodos de data legacy.

    Usa aggregation con `$group` apoyado en el índice compuesto
    (ejercicio, periodo) → segundos en lugar de minutos sobre 1M+ docs.
    """
    sii_match: dict = {}
    com_match: dict = {}
    if nif_titular:
        nif_norm = str(nif_titular).strip().upper()
        sii_match["nif_titular"] = nif_norm
        com_match["nif_titular"] = {"$in": [nif_norm, None]}

    async def _distinct_eje_per(col, match: dict):
        pipeline = []
        if match:
            pipeline.append({"$match": match})
        pipeline.append({"$group": {
            "_id": {"ejercicio": "$ejercicio", "periodo": "$periodo"},
        }})
        cursor = col.aggregate(pipeline)
        docs = await cursor.to_list(length=None)
        eje = {d["_id"].get("ejercicio") for d in docs if d["_id"].get("ejercicio")}
        per = {d["_id"].get("periodo") for d in docs if d["_id"].get("periodo")}
        return eje, per

    sii_eje, sii_per = await _distinct_eje_per(_db.facturas_sii, sii_match)
    com_eje, com_per = await _distinct_eje_per(_db.facturas_comercial, com_match)
    ejercicios = sorted({str(e) for e in (sii_eje | com_eje)})
    periodos = sorted({str(p) for p in (sii_per | com_per)})
    return {"ejercicios": ejercicios, "periodos": periodos}


@router.get("/comparativa/resumen-origenes")
async def comparativa_resumen_origenes(
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    num_serie: Optional[str] = None,
    nif_titular: Optional[str] = None,
):
    """Resumen agregado por `origen_comercial` (SAP / SIGLO / desconocido).

    Devuelve por cada origen:
      - total_facturas (count)
      - base_total / cuota_total / importe_total (sumas)
      - matches_sii / sin_match_sii (cuántas tienen contrapartida en SII)
      - discrepancias / coincidencias (sólo entre las que tienen match)

    Soporta los mismos filtros que `/comparativa`: ejercicio, periodo,
    num_serie (contiene), nif_titular. Pensado para una banda de tarjetas KPI
    encima de la tabla.
    """
    import re

    config = await _load_comparativa_config()
    filtro_com: dict = {}
    if ejercicio:
        filtro_com["ejercicio"] = str(ejercicio)
    if periodo:
        periodos_list = [
            p.strip() for p in str(periodo).split(",") if p.strip()
        ]
        if len(periodos_list) == 1:
            filtro_com["periodo"] = periodos_list[0]
        elif len(periodos_list) > 1:
            filtro_com["periodo"] = {"$in": periodos_list}
    if num_serie:
        filtro_com["num_serie_factura"] = {
            "$regex": re.escape(num_serie), "$options": "i",
        }
    if nif_titular:
        nif_norm = str(nif_titular).strip().upper()
        filtro_com["nif_titular"] = {"$in": [nif_norm, None]}
    else:
        nif_norm = None
    if config["excluir_comercial_base_cero"]:
        filtro_com["base_imponible"] = {"$nin": [0, 0.0, None]}

    # 1) Aggregation: cuentas y sumas por origen
    pipeline = [
        {"$match": filtro_com} if filtro_com else {"$match": {}},
        {"$group": {
            "_id": {"$ifNull": ["$origen_comercial", "desconocido"]},
            "total_facturas": {"$sum": 1},
            "base_total": {"$sum": {"$ifNull": ["$base_imponible", 0]}},
            "cuota_total": {"$sum": {"$ifNull": ["$cuota_repercutida", 0]}},
            "importe_total": {"$sum": {"$ifNull": ["$importe_total", 0]}},
        }},
        {"$sort": {"total_facturas": -1}},
    ]
    grupos = await _db.facturas_comercial.aggregate(pipeline).to_list(length=None)

    # 2) Para cada origen calculamos matches/discrepancias contra SII
    #    Cargamos las facturas comerciales del grupo (sólo num_serie),
    #    cruzamos con SII por num_serie ∈ comercial (uses unique index)
    #    y diff sólo de las que coinciden.
    resultados = []
    for g in grupos:
        origen_label = g["_id"]
        ftr = {**filtro_com}
        if origen_label == "desconocido":
            ftr["origen_comercial"] = {"$in": [None, ""]}
        else:
            ftr["origen_comercial"] = origen_label

        com_docs = await _db.facturas_comercial.find(
            ftr, {"_id": 0, "versiones": 0}
        ).to_list(length=None)
        com_keys = [d["num_serie_factura"] for d in com_docs]

        sii_docs = []
        if com_keys:
            sii_filter = {"num_serie_factura": {"$in": com_keys}}
            if ejercicio:
                sii_filter["ejercicio"] = str(ejercicio)
            if periodo:
                periodos_list = [
                    p.strip() for p in str(periodo).split(",") if p.strip()
                ]
                if len(periodos_list) == 1:
                    sii_filter["periodo"] = periodos_list[0]
                elif len(periodos_list) > 1:
                    sii_filter["periodo"] = {"$in": periodos_list}
            if nif_norm:
                sii_filter["nif_titular"] = nif_norm
            sii_docs = await _db.facturas_sii.find(
                sii_filter, {"_id": 0, "versiones": 0}
            ).to_list(length=None)
        sii_map = {d["num_serie_factura"]: d for d in sii_docs}

        matches = 0
        coincidencias = 0
        discrepancias = 0
        for com in com_docs:
            sii = sii_map.get(com["num_serie_factura"])
            if sii:
                matches += 1
                if not diff_facturas(sii, com, config):
                    coincidencias += 1
                else:
                    discrepancias += 1

        resultados.append({
            "origen": origen_label,
            "total_facturas": g["total_facturas"],
            "base_total": round(g.get("base_total") or 0, 2),
            "cuota_total": round(g.get("cuota_total") or 0, 2),
            "importe_total": round(g.get("importe_total") or 0, 2),
            "matches_sii": matches,
            "sin_match_sii": g["total_facturas"] - matches,
            "coincidencias": coincidencias,
            "discrepancias": discrepancias,
        })

    return {"items": resultados}


@router.get("/comparativa/nifs-titulares")
async def comparativa_nifs_titulares():
    """Devuelve la lista distinct de `nif_titular` presentes en SII y comercial,
    enriquecida con el `nombre_titular` desde el catálogo de sociedades.

    Útil para construir el toggle de "Sociedad" en la UI. Si en el comercial
    existen docs sin nif_titular (data legacy), se devuelve adicionalmente el
    contador `comercial_sin_nif` para que la UI pueda avisar al usuario.
    """
    sii_nifs = await _db.facturas_sii.distinct("nif_titular")
    com_nifs = await _db.facturas_comercial.distinct("nif_titular")
    nifs = sorted(
        {str(n).upper() for n in (sii_nifs or []) + (com_nifs or []) if n}
    )
    comercial_sin_nif = await _db.facturas_comercial.count_documents(
        {"$or": [
            {"nif_titular": None},
            {"nif_titular": ""},
            {"nif_titular": {"$exists": False}},
        ]}
    )
    # Enriquecer con nombre_titular desde el catálogo
    catalogo = await _cargar_catalogo_sociedades()
    nif_to_nombre: dict[str, str] = {}
    for soc, info in catalogo.items():
        nif_to_nombre[info["nif_titular"]] = info.get("nombre_titular") or ""
    sociedades = [
        {
            "nif_titular": n,
            "nombre_titular": nif_to_nombre.get(n, ""),
        }
        for n in nifs
    ]
    return {
        "nifs_titulares": nifs,
        "sociedades": sociedades,
        "comercial_sin_nif": comercial_sin_nif,
    }


@router.get("/comparativa/export")
async def comparativa_export(
    only_diffs: bool = True,
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    num_serie: Optional[str] = None,
    estado: Optional[str] = None,
    nif_titular: Optional[str] = None,
):
    """Exporta la comparativa filtrada a CSV (UTF-8 BOM) abrible en Excel.

    Implementación **streaming**: escribe filas conforme se generan en lugar
    de bufferizar todo en memoria. Mismas heurísticas que el listado
    paginado (`/comparativa`):
      - Universo construido desde comercial (pequeño) + matches SII.
      - `solo_sii` se itera mediante cursor en `facturas_sii`.

    Esto evita que exports grandes (cientos de miles de filas) saturen
    memoria del backend o el límite de 100s de Cloudflare.
    """
    config = await _load_comparativa_config()
    filtro_sii, filtro_com = await _build_filtros(
        ejercicio, periodo, num_serie,
        excluir_base_cero=config["excluir_comercial_base_cero"],
        nif_titular=nif_titular,
    )

    # Cabeceras CSV
    headers = ["num_serie_factura", "estado", "campos_con_diferencias"]
    for c in CAMPOS_CANONICOS:
        if c == "num_serie_factura":
            continue
        headers.append(f"sii_{c}")
        headers.append(f"com_{c}")

    def _row_to_cells(r: dict) -> list:
        cells = [
            r["num_serie_factura"],
            r["estado"],
            ",".join(r["diferencias"].keys()),
        ]
        sii = r.get("sii") or {}
        com = r.get("comercial") or {}
        for c in CAMPOS_CANONICOS:
            if c == "num_serie_factura":
                continue
            cells.append(sii.get(c, "") if sii.get(c) is not None else "")
            cells.append(com.get(c, "") if com.get(c) is not None else "")
        return cells

    def _writerow_to_str(cells: list) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(cells)
        return buf.getvalue()

    async def _generate():
        # BOM para Excel + cabecera
        yield ("\ufeff" + _writerow_to_str(headers)).encode("utf-8")

        # 1) Universo comercial completo (pequeño)
        com_docs = await _db.facturas_comercial.find(
            filtro_com, {"_id": 0, "versiones": 0}
        ).to_list(length=None)
        com_map = {d["num_serie_factura"]: d for d in com_docs}
        com_keys = list(com_map.keys())

        # 2) Matches SII por num_serie ∈ comercial
        sii_match_docs = []
        if com_keys:
            sii_match_docs = await _db.facturas_sii.find(
                {**filtro_sii, "num_serie_factura": {"$in": com_keys}},
                {"_id": 0, "versiones": 0},
            ).to_list(length=None)
        sii_match_map = {d["num_serie_factura"]: d for d in sii_match_docs}

        # 3) Filas comerciales (coincide / discrepancia / solo_comercial)
        for ns in com_keys:
            r = _build_row_from_docs(
                sii_match_map.get(ns), com_map.get(ns), ns, config,
            )
            if estado and r["estado"] != estado:
                continue
            if estado is None and only_diffs and r["estado"] == "coincide":
                continue
            yield _writerow_to_str(_row_to_cells(r)).encode("utf-8")

        # 4) Filas solo_sii: cursor incremental en SII fuera de comercial.
        #    Sólo si el usuario las quiere ver (estado=None engloba "diffs"
        #    y "all" — ambos incluyen solo_sii; estado=solo_sii filtra a
        #    sólo esas).
        incluir_solo_sii = estado in (None, "solo_sii")
        if incluir_solo_sii:
            solo_sii_filter = dict(filtro_sii)
            ns_clause = dict(solo_sii_filter.get("num_serie_factura") or {})
            ns_clause["$nin"] = com_keys
            solo_sii_filter["num_serie_factura"] = ns_clause
            cursor = _db.facturas_sii.find(
                solo_sii_filter, {"_id": 0, "versiones": 0}
            )
            async for d in cursor:
                r = _build_row_from_docs(
                    d, None, d["num_serie_factura"], config,
                )
                if estado and r["estado"] != estado:
                    continue
                yield _writerow_to_str(_row_to_cells(r)).encode("utf-8")

    filename = "comparativa"
    if nif_titular:
        filename += f"_{nif_titular}"
    if ejercicio:
        filename += f"_{ejercicio}"
    if periodo:
        filename += f"_{periodo}"
    filename += ".csv"

    return StreamingResponse(
        _generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Jobs (consulta mensual asíncrona con progreso)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ejecutar_consulta_mensual_job(
    job_id: str,
    cert_bytes: Optional[bytes],
    cert_password: Optional[str],
    nif_titular: str,
    nombre_titular: str,
    ejercicio: str,
    periodo: str,
    entorno: str,
    max_paginas: Optional[int] = None,
    start_clave: Optional[dict] = None,
    start_pagina: int = 0,
    start_invoices: int = 0,
):
    """Worker que ejecuta la consulta mensual en background y va actualizando
    el documento del job en Mongo."""
    loop = asyncio.get_running_loop()

    async def _update_and_check(pag, acum, clave, facturas_pagina):
        # Commit incremental: persistimos las facturas de ESTA página con
        # bulk_write (1 round-trip Mongo) antes de actualizar el progreso.
        if facturas_pagina:
            try:
                await upsert_facturas_bulk(
                    "facturas_sii", facturas_pagina, "consulta_mensual"
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "upsert_facturas_bulk falló — se sigue con el progreso"
                )
        upd = {
            "progress.page": pag,
            "progress.invoices": acum,
            "updated_at": _now_iso(),
        }
        if clave is not None:
            upd["progress.clave_paginacion"] = clave
        await _db.jobs.update_one({"id": job_id}, {"$set": upd})
        doc = await _db.jobs.find_one(
            {"id": job_id}, {"_id": 0, "cancel_requested": 1}
        )
        return bool(doc and doc.get("cancel_requested"))

    def _update_progress(pagina: int, acumuladas: int, clave=None,
                          facturas_pagina=None) -> bool:
        """Persiste las facturas de la página y devuelve True si el usuario
        ha solicitado cancelar el job."""
        fut = asyncio.run_coroutine_threadsafe(
            _update_and_check(pagina, acumuladas, clave, facturas_pagina), loop
        )
        try:
            return bool(fut.result(timeout=600))
        except Exception:  # noqa: BLE001
            _logger.exception("No se pudo actualizar progreso del job")
            return False

    start_ts = datetime.now(timezone.utc)
    await _db.jobs.update_one(
        {"id": job_id},
        {"$set": {"status": "running", "started_at": _now_iso()}},
    )
    log_entry = {
        "id": uuid.uuid4().hex,
        "timestamp": start_ts.isoformat(),
        "operation": "ConsultaLRFacturasEmitidas.Mensual",
        "endpoint": ENDPOINTS.get(entorno, ""),
        "entorno": entorno,
        "status": "ok", "http_status": None, "error_message": None,
        "duration_ms": 0, "request_xml": "", "response_xml": "",
        "nif_titular": nif_titular, "nif_emisor": nif_titular,
        "num_serie_factura": None, "consulta_id": None,
        "batch_id": f"job:{job_id}",
    }

    try:
        try:
            client = build_client(
                cert_bytes=cert_bytes, cert_password=cert_password
            )
        except ValueError as exc:
            raise RuntimeError(f"Certificado inválido: {exc}") from exc

        def _run():
            return _consultar_mensual_real(
                client, nif_titular, nombre_titular, ejercicio, periodo,
                entorno, progress_cb=_update_progress,
                max_paginas=max_paginas,
                start_clave=start_clave,
                start_pagina=start_pagina,
                start_invoices=start_invoices,
            )
        try:
            facturas, req_xml, resp_xml = await asyncio.to_thread(_run)
            log_entry["request_xml"] = _truncar_xml(req_xml)
            log_entry["response_xml"] = _truncar_xml(resp_xml)
            log_entry["http_status"] = 200
        except Exception as exc:  # noqa: BLE001
            log_entry["status"] = "error"
            log_entry["error_message"] = str(exc)[:2000]
            log_entry["request_xml"] = _truncar_xml(
                getattr(exc, "request_xml", "") or ""
            )
            log_entry["response_xml"] = _truncar_xml(
                getattr(exc, "response_xml", "") or ""
            )
            log_entry["http_status"] = 502
            _logger.exception("Fallo SOAP en consulta mensual job")
            raise

        # Las facturas se guardan página a página via `progress_cb` durante
        # la ejecución de `_consultar_mensual_real`. No hay que persistirlas
        # aquí (eso provocaría dobles upserts).

        # ¿Se solicitó cancelar a mitad del job?
        doc = await _db.jobs.find_one({"id": job_id}, {"cancel_requested": 1})
        final_status = "cancelled" if doc and doc.get("cancel_requested") else "completed"
        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "status": final_status,
                "finished_at": _now_iso(),
                "result": {
                    "total": len(facturas),
                    "ejercicio": ejercicio,
                    "periodo": periodo,
                },
            }},
        )
    except Exception as exc:  # noqa: BLE001
        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "status": "failed",
                "finished_at": _now_iso(),
                "error_message": str(exc)[:2000],
            }},
        )
    finally:
        log_entry["duration_ms"] = int(
            (datetime.now(timezone.utc) - start_ts).total_seconds() * 1000
        )
        log_entry["request_xml"] = _truncar_xml(log_entry.get("request_xml", ""))
        log_entry["response_xml"] = _truncar_xml(log_entry.get("response_xml", ""))
        try:
            await _db.wslogs.insert_one(log_entry)
        except Exception:  # noqa: BLE001
            _logger.exception("No se pudo guardar log de job mensual")


@router.post("/sii/consulta-mensual-async")
async def consulta_mensual_async(
    nif_titular: str = Form(...),
    nombre_titular: str = Form(...),
    ejercicio: str = Form(...),
    periodo: str = Form(...),
    entorno: str = Form("preproduccion"),
    cert_password: Optional[str] = Form(None),
    certificate: Optional[UploadFile] = File(None),
    max_paginas: Optional[int] = Form(None),
):
    """Versión asíncrona de la consulta mensual.

    Lanza un job en background y devuelve un `job_id` que el cliente puede
    consultar con `GET /api/jobs/{job_id}` para ver el progreso (página actual,
    facturas acumuladas, status, error).
    """
    cert_bytes = None
    if certificate is not None:
        cert_bytes = await certificate.read()
        if not cert_bytes:
            cert_bytes = None
    job_id = uuid.uuid4().hex
    job_doc = {
        "id": job_id,
        "type": "consulta-mensual",
        "status": "queued",
        "progress": {"page": 0, "invoices": 0},
        "params": {
            "nif_titular": nif_titular,
            "nombre_titular": nombre_titular,
            "ejercicio": ejercicio,
            "periodo": periodo,
            "entorno": entorno,
            "max_paginas": max_paginas,
        },
        "result": None,
        "error_message": None,
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "updated_at": _now_iso(),
    }
    await _db.jobs.insert_one(job_doc)

    # Lanza el worker sin esperar
    asyncio.create_task(
        _ejecutar_consulta_mensual_job(
            job_id, cert_bytes, cert_password, nif_titular, nombre_titular,
            ejercicio, periodo, entorno, max_paginas,
        )
    )
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
async def obtener_job(job_id: str):
    doc = await _db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Job no encontrado")
    return doc

@router.post("/jobs/{job_id}/resume")
async def reanudar_job(
    job_id: str,
    cert_password: Optional[str] = Form(None),
    certificate: Optional[UploadFile] = File(None),
):
    """Reanuda un job en estado `cancelled` o `failed` desde la última
    `ClavePaginacion` que el worker guardó en BD. Crea un nuevo job que
    continúa la descarga desde ese punto (no re-descarga las páginas
    anteriores). Requiere subir el certificado de nuevo si era modo real."""
    doc = await _db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Job no encontrado")
    if doc.get("status") not in ("cancelled", "failed"):
        raise HTTPException(
            400,
            f"Sólo se pueden reanudar jobs cancelled/failed (estado actual: "
            f"{doc.get('status')})",
        )
    clave = (doc.get("progress") or {}).get("clave_paginacion")
    if not clave:
        raise HTTPException(
            400,
            "Este job no tiene punto de continuación guardado "
            "(probablemente falló antes de completar la primera página).",
        )

    cert_bytes = None
    if certificate is not None:
        cert_bytes = await certificate.read()
        if not cert_bytes:
            cert_bytes = None
    p = doc.get("params") or {}
    if not cert_bytes:
        raise HTTPException(
            400,
            "El job original era en modo real: vuelve a aportar el "
            "certificado (.pfx) para reanudar.",
        )
    new_id = uuid.uuid4().hex
    job_doc = {
        "id": new_id,
        "type": "consulta-mensual",
        "status": "queued",
        "progress": {
            "page": doc.get("progress", {}).get("page", 0),
            "invoices": doc.get("progress", {}).get("invoices", 0),
            "clave_paginacion": clave,
        },
        "params": {**p},
        "result": None,
        "error_message": None,
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "updated_at": _now_iso(),
        "resumed_from": job_id,
    }
    await _db.jobs.insert_one(job_doc)
    asyncio.create_task(
        _ejecutar_consulta_mensual_job(
            new_id, cert_bytes, cert_password,
            p["nif_titular"], p["nombre_titular"],
            p["ejercicio"], p["periodo"], p["entorno"],
            p.get("max_paginas"), start_clave=clave,
            start_pagina=doc.get("progress", {}).get("page", 0),
            start_invoices=doc.get("progress", {}).get("invoices", 0),
        )
    )
    return {
        "job_id": new_id,
        "status": "queued",
        "resumed_from": job_id,
        "start_from_page": doc.get("progress", {}).get("page", 0),
    }




@router.post("/jobs/{job_id}/cancel")
async def cancelar_job(job_id: str):
    """Solicita la cancelación cooperativa de un job en background.
    El worker detectará el flag tras finalizar la página en curso y dejará
    el job en estado `cancelled` con todas las facturas ya descargadas
    persistidas en `facturas_sii`."""
    doc = await _db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Job no encontrado")
    if doc.get("status") not in ("queued", "running"):
        return {
            "id": job_id,
            "status": doc.get("status"),
            "cancel_requested": doc.get("cancel_requested", False),
            "message": "El job ya terminó, no se puede cancelar",
        }
    await _db.jobs.update_one(
        {"id": job_id},
        {"$set": {"cancel_requested": True, "updated_at": _now_iso()}},
    )
    return {"id": job_id, "status": doc.get("status"), "cancel_requested": True}


@router.get("/jobs")
async def listar_jobs(limit: int = 20):
    cur = _db.jobs.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
    items = await cur.to_list(length=limit)
    return {"items": items}
