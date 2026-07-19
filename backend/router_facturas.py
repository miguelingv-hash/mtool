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
import re
import time
import uuid

from auth import get_current_user, require_permission
from imports_log import (
    add_import_errors,
    finish_import,
    start_import,
)

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

# ---------------------------------------------------------------------------
# Micro-cache in-memory para /comparativa (TTL corto)
# ---------------------------------------------------------------------------
# El endpoint /comparativa carga TODOS los docs de `facturas_comercial` en scope
# para construir la vista (~14s con 485k docs). Al abrir la Comparativa el
# frontend dispara múltiples peticiones simultáneas (por totales, resumen,
# nifs, etc.) que se solapan y saturan el pod → 502 del ingress.
#
# Cache con TTL=15s por tupla de parámetros: si otra petición idéntica llega
# dentro de la ventana, sirve del cache (instantáneo). Si llega mientras se
# está calculando la primera, espera al Future en curso (single-flight).
import asyncio as _asyncio  # alias para no chocar con otros imports
_COMPARATIVA_CACHE: dict[tuple, tuple[float, object]] = {}
_COMPARATIVA_INFLIGHT: dict[tuple, "_asyncio.Future"] = {}
# TTL de 5 min: los datos de comparativa sólo cambian tras un import
# (el propio import invalida el cache expresamente via `invalidate_comparativa_cache`).
# Con datasets masivos (>1M docs), la primera carga tarda ~30-50s, así que
# vale la pena mantenerlo en cache durante toda la sesión de trabajo del usuario.
_COMPARATIVA_TTL_S = 300.0
_COMPARATIVA_MAX_ENTRIES = 64  # LRU pequeño; queries típicas son pocas


def _comparativa_cache_get(key: tuple):
    """Devuelve el valor cacheado si está vigente, o None si expiró/no está."""
    import time
    ent = _COMPARATIVA_CACHE.get(key)
    if not ent:
        return None
    ts, val = ent
    if time.monotonic() - ts > _COMPARATIVA_TTL_S:
        _COMPARATIVA_CACHE.pop(key, None)
        return None
    return val


def _comparativa_cache_put(key: tuple, val):
    """Persiste el valor en cache; recorta si excedemos el máximo."""
    import time
    if len(_COMPARATIVA_CACHE) >= _COMPARATIVA_MAX_ENTRIES:
        # Evict la entrada más antigua (LRU aproximado por timestamp).
        oldest = min(_COMPARATIVA_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _COMPARATIVA_CACHE.pop(oldest, None)
    _COMPARATIVA_CACHE[key] = (time.monotonic(), val)


def invalidate_comparativa_cache():
    """Vaciamos el cache tras un import (los datos cambiaron)."""
    _COMPARATIVA_CACHE.clear()


async def _cached_or_compute(cache_key: tuple, compute_coro_factory):
    """Helper genérico cache + single-flight para endpoints de comparativa.

    - Si la key está en cache y vigente → devuelve el valor cacheado.
    - Si hay una computación en vuelo con la misma key → aguarda a su Future.
    - Si no → dispara la computación, guarda el resultado, lo devuelve.
    """
    import time
    hit = _comparativa_cache_get(cache_key)
    if hit is not None:
        return hit
    inflight = _COMPARATIVA_INFLIGHT.get(cache_key)
    if inflight is not None:
        return await inflight
    fut: _asyncio.Future = _asyncio.get_event_loop().create_future()
    _COMPARATIVA_INFLIGHT[cache_key] = fut
    try:
        t0 = time.monotonic()
        result = await compute_coro_factory()
        dur = time.monotonic() - t0
        if _logger and dur > 3:
            _logger.info("[cache-miss] key=%s dur=%.2fs", cache_key, dur)
        _comparativa_cache_put(cache_key, result)
        fut.set_result(result)
        return result
    except Exception as e:
        fut.set_exception(e)
        raise
    finally:
        _COMPARATIVA_INFLIGHT.pop(cache_key, None)


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
        # Índice por nif_titular — el filtro más habitual de la comparativa.
        # Con 485k+ docs en facturas_comercial, sin este índice el collscan
        # tarda 6-14s por request y el frontend triggerea 502 en el ingress
        # cuando dispara varias queries en paralelo al abrir la vista.
        await _db.facturas_sii.create_index("nif_titular")
        await _db.facturas_comercial.create_index("nif_titular")
        # Compuesto para la query típica del listado principal.
        await _db.facturas_sii.create_index([
            ("nif_titular", 1), ("ejercicio", 1), ("periodo", 1),
        ])
        await _db.facturas_comercial.create_index([
            ("nif_titular", 1), ("ejercicio", 1), ("periodo", 1),
        ])
        await _db.jobs.create_index("id", unique=True)
        await _db.jobs.create_index([("status", 1), ("created_at", -1)])
        # Audit trail — índices para el listado/filtros del historial de imports.
        from imports_log import ensure_indexes as _audit_indexes  # noqa: WPS433
        await _audit_indexes(_db)
        # iter25: índices compuestos que aprovechan el `tipo_factura`
        # denormalizado en `facturas_comercial`. Sub-segundo para filtros
        # comunes por (sociedad, ejercicio, periodo, tipo_factura).
        from backfill_tipo_factura import (  # noqa: WPS433
            ensure_indexes_iter25,
            ensure_indexes_iter26,
        )
        await ensure_indexes_iter25(_db, _logger)
        await ensure_indexes_iter26(_db, _logger)
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
    # Warm-up asíncrono del cache de la Comparativa. Corre en background sin
    # bloquear el startup. Precalcula las combinaciones más usadas para que
    # la primera visita del usuario a la Comparativa no dispare 3-5 queries
    # pesadas en paralelo (17s+) que saturan el event loop y hacen que el
    # ingress devuelva 502.
    _asyncio.create_task(_warmup_comparativa_cache())


async def _warmup_comparativa_cache():
    """Precalienta el cache in-memory de los endpoints pesados de la
    Comparativa para el conjunto de NIFs presentes en BD.

    Se ejecuta al arranque (invocada desde `cleanup_orphan_jobs`) y va poblando
    el cache secuencialmente (no en paralelo) para no saturar el event loop.
    Ignora errores silenciosamente — es puramente una optimización.
    """
    if _db is None:
        return
    try:
        # Pequeño retardo para dejar que la app termine el startup por completo
        # antes de meterle carga (evita competir con los seeds/índices).
        await _asyncio.sleep(3)
        # NIFs presentes en BD (los que verá el usuario en el toggle).
        # Ojo: NO precalentamos con nif=None (agregado de TODAS las sociedades),
        # porque esa query recorre el universo completo (~1.4M docs) y tarda
        # 15-20s. El frontend hoy en día siempre filtra por una sociedad
        # concreta (autoselección de la 1ª al montar), así que la key con
        # None sólo se usaría muy ocasionalmente y no compensa el warmup.
        nifs = await _db.facturas_comercial.distinct("nif_titular")
        nifs = [n for n in nifs if n]
        _logger.info(
            "[warmup] precalentando cache de la Comparativa para %d NIF(s)…",
            len(nifs),
        )
        # 1) Warmup del bundle base (sin filtro de periodo/ejercicio) SÓLO
        #    para NIFs con volumen manejable. Con el refactor 2026-02 el
        #    bundle usa agregación nativa Mongo → soportamos hasta 1.5M
        #    docs (~50s de warmup por NIF, aceptable en background).
        WARMUP_BASE_LIMIT = 1_500_000
        for nif in nifs:
            try:
                n_com = await _db.facturas_comercial.count_documents({"nif_titular": nif})
                if n_com > WARMUP_BASE_LIMIT:
                    _logger.info(
                        "[warmup] omitiendo bundle base nif=%s (%d docs > %d)",
                        nif, n_com, WARMUP_BASE_LIMIT,
                    )
                    continue
                await _cached_or_compute(
                    ("bundle", 0, 50, True, None, None, None, None, None, "desc", nif),
                    lambda n=nif: _comparativa_bundle_impl(
                        skip=0, limit=50, only_diffs=True, ejercicio=None,
                        periodo=None, num_serie=None, estado=None,
                        sort_by=None, sort_dir="desc", nif_titular=n,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                _logger.warning("[warmup] fallo bundle base para nif=%s: %s", nif, e)

        # 2) Warmup por combinaciones (nif, ejercicio, periodo) con datos —
        # imprescindible cuando el dataset está concentrado en pocos periodos
        # (p.ej. todo junio 2026). Sin esto la 1ª vez que el user filtra por
        # mes seguiría tardando ~20s y sacando 502 en el ingress.
        COMBO_WARMUP_LIMIT = 1_500_000
        for nif in nifs:
            try:
                pipeline = [
                    {"$match": {"nif_titular": nif}},
                    {"$group": {
                        "_id": {"ejercicio": "$ejercicio", "periodo": "$periodo"},
                        "count": {"$sum": 1},
                    }},
                    {"$match": {"count": {"$gte": 500, "$lte": COMBO_WARMUP_LIMIT}}},
                    {"$sort": {"count": -1}},
                    {"$limit": 24},
                ]
                combos = []
                async for d in _db.facturas_comercial.aggregate(pipeline):
                    combos.append((d["_id"].get("ejercicio"), d["_id"].get("periodo")))
                _logger.info(
                    "[warmup] nif=%s: %d combinaciones (ejercicio,periodo) con datos…",
                    nif, len(combos),
                )
                for eje, per in combos:
                    if not eje or not per:
                        continue
                    try:
                        # Warmup del bundle completo para esa combinación —
                        # así al filtrar por mes en la UI la respuesta llega
                        # instantánea (cache hit del bundle Y de sus 3 partes).
                        await _cached_or_compute(
                            ("bundle", 0, 50, True, str(eje), str(per), None, None, None, "desc", nif),
                            lambda n=nif, e=eje, p=per: _comparativa_bundle_impl(
                                skip=0, limit=50, only_diffs=True,
                                ejercicio=str(e), periodo=str(p),
                                num_serie=None, estado=None,
                                sort_by=None, sort_dir="desc",
                                nif_titular=n,
                            ),
                        )
                    except Exception as e:  # noqa: BLE001
                        _logger.warning(
                            "[warmup] fallo bundle nif=%s eje=%s per=%s: %s",
                            nif, eje, per, e,
                        )
            except Exception as e:  # noqa: BLE001
                _logger.warning("[warmup] fallo combos nif=%s: %s", nif, e)

        _logger.info("[warmup] Comparativa lista.")
    except Exception as e:  # noqa: BLE001
        _logger.warning("[warmup] abortado: %s", e)


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
    # Los datos cambiaron → invalidamos el cache in-memory de /comparativa.
    invalidate_comparativa_cache()


async def upsert_facturas_bulk(coleccion: str, datos_list: list, fuente: str):
    """Upsert masivo de facturas en una sola operación `bulk_write`.

    Para jobs mensuales con miles de facturas por página, esto reduce ~10000
    round-trips a 1. NO mantiene histórico de versiones (`$push`) para no
    inflar los documentos: prima la velocidad de descarga sobre la auditoría
    versionada. El histórico sigue disponible para upserts unitarios.

    iter28: para `facturas_comercial`, si el doc tiene `detalle_iva` pero
    NO tiene `importe_total` (o es 0), lo derivamos como suma completa de
    (base + cuota) de cada línea. SIGLO no reporta importe_total en su CSV,
    y sin ese campo la reconciliación por importe canónico falla cuando el
    SII declara importe_total distinto de base+cuota (facturas con partes
    exentas / no sujetas).
    """
    from pymongo import UpdateOne  # noqa: WPS433
    if not datos_list:
        return
    now = datetime.now(timezone.utc).isoformat()
    es_comercial = coleccion == "facturas_comercial"
    ops = []
    for d in datos_list:
        if not d.get("num_serie_factura"):
            continue
        # iter28: auto-derivar importe_total en comerciales si falta.
        if es_comercial:
            det = d.get("detalle_iva") or []
            actual = d.get("importe_total")
            if det and (actual is None or actual == 0 or actual == 0.0):
                total = 0.0
                for line in det:
                    try:
                        b = float(line.get("base_imponible") or 0)
                        c = float(line.get("cuota_repercutida") or 0)
                        total += b + c
                    except (TypeError, ValueError):
                        pass
                if abs(total) > 0.01:
                    d["importe_total"] = round(total, 2)
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
        # NO invalidamos aquí — durante un import masivo, `upsert_facturas_bulk`
        # se llama en batches (cada 2000 facturas), y cada batch invalidaría
        # el cache dejando al frontend haciendo cache-miss constante mientras
        # dura el import (varios minutos). El cache expira solo por TTL (15s),
        # así que la Comparativa igualmente se refresca <15s tras terminar
        # el import. Si se necesita refresco inmediato, el propio endpoint que
        # cierre el import puede llamar a invalidate_comparativa_cache().


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
    "CausaExencion": "causa_exencion",
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
    user: dict = Depends(get_current_user),
):
    """Igual que `/sii/conciliar-newman` pero INSERTA en `facturas_sii` las
    facturas del CSV que no estén ya en BD. Usa `upsert_facturas_bulk` con
    `fuente: "conciliacion_newman"` para que sean trazables.

    Idempotente: si todas las facturas del CSV ya están en BD, no inserta nada.
    """
    contenido = await file.read()
    if not contenido:
        raise HTTPException(400, "El CSV está vacío")

    import_id = await start_import(
        _db,
        origen="sii",
        fuente="conciliacion_newman",
        file_name=file.filename,
        file_size_bytes=len(contenido),
        user_id=user.get("_id") or user.get("id"),
        user_email=user.get("email"),
        nif_titular=nif_titular,
        ejercicio=ejercicio,
        periodo=periodo,
    )

    try:
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

        if errores:
            await add_import_errors(_db, import_id, errores)

        await finish_import(
            _db, import_id, status="done",
            total_procesados=len(filas),
            insertados=insertadas,
            actualizados=0,
            extra={"ya_en_bd": len(existentes)},
        )

        return {
            "filtro": {"nif_titular": nif_titular, "ejercicio": ejercicio, "periodo": periodo},
            "total_csv": len(filas),
            "ya_en_bd": len(existentes),
            "insertadas": insertadas,
            "errores_csv": errores[:50],
            "import_id": import_id,
        }
    except HTTPException as exc:
        await finish_import(
            _db, import_id, status="error",
            error_message=f"HTTP {exc.status_code}: {exc.detail}",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        await finish_import(
            _db, import_id, status="error",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise


async def _ejecutar_importar_faltantes_job(
    job_id: str,
    contenido: bytes,
    nif_titular: str,
    nombre_titular: str,
    ejercicio: Optional[str],
    periodo: Optional[str],
    import_id: Optional[str] = None,
) -> None:
    """Worker en background del import masivo desde CSV Newman.

    Actualiza `jobs[job_id].progress.{processed, total, phase}` y `status`.
    Reusa la misma lógica del endpoint síncrono pero sin restricción de tiempo
    HTTP (Cloudflare corta a ~100s). Si `import_id` viene informado, cierra
    también el audit trail (`imports_log`) al terminar.
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
                    "import_id": import_id,
                },
                "finished_at": _now_iso(),
                "updated_at": _now_iso(),
                "progress.phase": "done",
            }},
        )

        if import_id:
            if errores:
                await add_import_errors(_db, import_id, errores)
            await finish_import(
                _db, import_id, status="done",
                total_procesados=len(filas),
                insertados=insertadas,
                actualizados=0,
                extra={"ya_en_bd": len(existentes)},
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
        if import_id:
            await finish_import(
                _db, import_id, status="error",
                error_message=f"{type(e).__name__}: {e}",
            )


@router.post("/sii/conciliar-newman/importar-faltantes-async")
async def conciliar_newman_importar_async(
    file: UploadFile = File(...),
    nif_titular: str = Form(...),
    nombre_titular: str = Form(""),
    ejercicio: Optional[str] = Form(None),
    periodo: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
):
    """Versión asíncrona de `/importar-faltantes`. Encola un job en background
    y devuelve un `job_id` que el cliente consulta con `GET /api/jobs/{id}`.

    Imprescindible para CSVs grandes (cientos de miles de filas) porque
    Cloudflare corta conexiones HTTP idle a ~100s.

    Streaming a disco: leemos el upload en chunks de 1 MB y lo escribimos a
    un fichero temporal, en lugar de cargar todo en memoria con
    `await file.read()`. Esto evita OOM en hosts modestos (EC2 t3.small con
    2 GB) cuando el CSV pesa varios cientos de MB — antes, parsear un CSV de
    180 MB con 865 k filas disparaba el OOM-killer del kernel y el backend
    moría a mitad de upload (axios reportaba ECONNABORTED).
    """
    import os
    import tempfile

    # 1) Volcar el upload a un fichero temporal en chunks de 1 MB
    tmp = tempfile.NamedTemporaryFile(
        prefix="newman_", suffix=".csv", delete=False,
    )
    total_bytes = 0
    try:
        chunk_size = 1024 * 1024  # 1 MB
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            tmp.write(chunk)
            total_bytes += len(chunk)
        tmp.flush()
        tmp.close()
        if total_bytes == 0:
            raise HTTPException(400, "El CSV está vacío")
    except Exception:
        # Si algo falla mientras subimos, limpiamos el temporal y propagamos.
        try:
            tmp.close()
            os.unlink(tmp.name)
        except OSError:
            pass
        raise

    job_id = uuid.uuid4().hex

    # Audit trail: creamos el registro AHORA para que el usuario pueda ver la
    # importación en curso desde el historial mientras el worker corre.
    import_id = await start_import(
        _db,
        origen="sii",
        fuente="conciliacion_newman_async",
        file_name=file.filename,
        file_size_bytes=total_bytes,
        user_id=user.get("_id") or user.get("id"),
        user_email=user.get("email"),
        nif_titular=nif_titular,
        ejercicio=ejercicio,
        periodo=periodo,
        job_id=job_id,
    )

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
            "file_size_bytes": total_bytes,
            "tmp_path": tmp.name,
            "import_id": import_id,
        },
        "result": None,
        "error_message": None,
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "updated_at": _now_iso(),
    }
    await _db.jobs.insert_one(job_doc)
    # El worker leerá el fichero desde disco y lo eliminará al terminar.
    asyncio.create_task(
        _ejecutar_importar_faltantes_job_desde_disco(
            job_id, tmp.name, nif_titular, nombre_titular, ejercicio, periodo,
            import_id=import_id,
        ),
    )
    return {"job_id": job_id, "status": "queued", "file_size_bytes": total_bytes, "import_id": import_id}


async def _ejecutar_importar_faltantes_job_desde_disco(
    job_id: str,
    tmp_path: str,
    nif_titular: str,
    nombre_titular: str,
    ejercicio: Optional[str],
    periodo: Optional[str],
    import_id: Optional[str] = None,
):
    """Wrapper del worker original que primero carga el CSV desde el fichero
    temporal y luego delega en `_ejecutar_importar_faltantes_job`. Garantiza
    que el temporal se borre incluso si el job falla (try/finally).
    """
    import os

    try:
        with open(tmp_path, "rb") as f:
            contenido = f.read()
        await _ejecutar_importar_faltantes_job(
            job_id, contenido, nif_titular, nombre_titular, ejercicio, periodo,
            import_id=import_id,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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
                # Fallback: la AEAT no siempre devuelve `<ImporteTotal>` en la
                # consulta masiva por período — típicamente NO lo trae para
                # facturas exentas (Sujeta.Exenta) pero también puede faltar
                # en F1 normales. Cuando falta, lo calculamos como
                # `base + cuota` (matemáticamente exacto). Sin este fallback
                # las exentas se guardaban con importe_total=None y no salían
                # en la Comparativa contra el CSV comercial.
                if total is None and base is not None:
                    total = float(base) + float(cuota or 0)
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
async def upload_csv_comercial(
    file: UploadFile = File(...),
    nif_titular_override: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
):
    """Sube un fichero comercial (SAP FI, SIGLO o CSV genérico) y lo guarda
    en `facturas_comercial`.

    Parámetro opcional `nif_titular_override`:
      Fuerza el `nif_titular` + `nombre_titular` de TODAS las filas al valor
      indicado, ignorando la columna `Soc.` del CSV. Imprescindible para
      reports SIGLO variante HC30 (extracto de balance) donde la columna
      `Soc.` contiene la clase de asiento (`HC30`, `NC`…) en lugar del
      código de sociedad SAP. El NIF debe estar en el catálogo
      `sociedades_catalogo`; en caso contrario se rechaza con 400.
    """
    if not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "Debe ser un archivo .csv o .txt")
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    # Audit trail — arrancamos el log ahora, lo cerraremos al final (finally).
    import_id = await start_import(
        _db,
        origen="comercial",
        fuente="ui_upload",
        file_name=file.filename,
        file_size_bytes=len(raw),
        user_id=user.get("_id") or user.get("id"),
        user_email=user.get("email"),
        nif_titular=(nif_titular_override or "").strip().upper() or None,
        extra={"nif_titular_override": bool(nif_titular_override)},
    )

    try:
        # Si viene override, lo validamos AHORA (antes del parseo) para dar
        # feedback rápido al usuario si el NIF no está en el catálogo.
        override_info: Optional[dict] = None
        if nif_titular_override:
            nif_norm = nif_titular_override.strip().upper()
            catalogo = await _cargar_catalogo_sociedades()
            # Buscamos el mapping por NIF en el catálogo (los seeds están indexados
            # por soc, no por NIF, así que hacemos búsqueda inversa).
            for _soc, info in catalogo.items():
                if info.get("nif_titular") == nif_norm:
                    override_info = {
                        "nif_titular": nif_norm,
                        "nombre_titular": info.get("nombre_titular") or "",
                    }
                    break
            if override_info is None:
                raise HTTPException(
                    400,
                    f"nif_titular_override={nif_norm!r} no está en el catálogo. "
                    f"Añádelo con PUT /api/admin/sociedades antes de subir.",
                )

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

        # Aplicar el override si se pidió — reemplaza nif/nombre en TODAS las
        # filas parseadas (incluyendo las que ya tenían un mapping desde Soc.).
        # También limpia el aviso "Sociedades no mapeadas" del report de errores
        # porque en modo override, la columna Soc. NO es la fuente de verdad.
        if override_info:
            for r in registros:
                r["nif_titular"] = override_info["nif_titular"]
                r["nombre_titular"] = override_info["nombre_titular"]
            errores = [
                e for e in errores
                if not (
                    e.get("fila") == -1
                    and "no encontradas en el catálogo" in e.get("motivo", "")
                )
            ]

        total = 0
        validos: list[dict] = []
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
            validos.append(norm)

        # Bulk upsert (una sola operación bulk_write en lugar de N round-trips).
        # Para 12800 filas pasamos de ~30s a <2s. Sin esto los uploads grandes
        # rebasaban el timeout de Cloudflare (100s) y devolvían 502.
        if validos:
            await upsert_facturas_bulk(
                "facturas_comercial", validos, "csv_comercial",
            )
            total = len(validos)

        # Persistir errores en el audit trail (recortados a 100 en el módulo).
        if errores:
            await add_import_errors(_db, import_id, errores)

        # Tras importar, hacemos match con facturas_sii y devolvemos un mini
        # resumen para que el frontend muestre el resultado de la comparativa.
        nums = [r["num_serie_factura"] for r in registros if r.get("num_serie_factura")]
        matched_count = 0
        if nums:
            matched_count = await _db.facturas_sii.count_documents(
                {"num_serie_factura": {"$in": nums}}
            )

        await finish_import(
            _db, import_id,
            status="done",
            total_procesados=total,
            insertados=total,  # upserts individuales, no distinguimos insert/update aquí
            actualizados=0,
            extra={
                "origen_detectado": origen_detectado,
                "matches_sii": matched_count,
                "sin_match_sii": max(0, len(nums) - matched_count),
            },
        )

        return {
            "total": total,
            "errores": errores,
            "origen": origen_detectado,
            "matches_sii": matched_count,
            "sin_match_sii": max(0, len(nums) - matched_count),
            "import_id": import_id,
        }
    except HTTPException as exc:
        await finish_import(
            _db, import_id, status="error",
            error_message=f"HTTP {exc.status_code}: {exc.detail}",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        await finish_import(
            _db, import_id, status="error",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise


@router.post("/comercial/csv-async")
async def upload_csv_comercial_async(
    file: UploadFile = File(...),
    nif_titular_override: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
):
    """Versión asíncrona de `/comercial/csv` para ficheros grandes.

    Igual patrón que Newman:
      1) Volcamos el upload a un fichero temporal en chunks de 1 MB (no RAM).
      2) Encolamos un job en `_db.jobs` y devolvemos `job_id` + `import_id`.
      3) Worker procesa en background (bulk_write) sin tocar el request HTTP,
         así Cloudflare (~100s idle timeout) no corta.

    El cliente hace polling con `GET /api/jobs/{job_id}` hasta ver
    `status == "done"` o `"error"`.
    """
    import os
    import tempfile

    if not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "Debe ser un archivo .csv o .txt")

    # 1) Streaming a disco (1 MB chunks) para no cargar el fichero en RAM.
    tmp = tempfile.NamedTemporaryFile(
        prefix="comercial_", suffix=".csv", delete=False,
    )
    total_bytes = 0
    try:
        chunk_size = 1024 * 1024
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            tmp.write(chunk)
            total_bytes += len(chunk)
        tmp.flush()
        tmp.close()
        if total_bytes == 0:
            raise HTTPException(400, "El CSV está vacío")
    except Exception:
        try:
            tmp.close()
            os.unlink(tmp.name)
        except OSError:
            pass
        raise

    job_id = uuid.uuid4().hex
    import_id = await start_import(
        _db,
        origen="comercial",
        fuente="ui_upload_async",
        file_name=file.filename,
        file_size_bytes=total_bytes,
        user_id=user.get("_id") or user.get("id"),
        user_email=user.get("email"),
        nif_titular=(nif_titular_override or "").strip().upper() or None,
        job_id=job_id,
        extra={"nif_titular_override": bool(nif_titular_override)},
    )

    job_doc = {
        "id": job_id,
        "type": "comercial-csv-import",
        "status": "queued",
        "progress": {"processed": 0, "total": 0, "phase": "queued"},
        "params": {
            "file_name": file.filename,
            "file_size_bytes": total_bytes,
            "tmp_path": tmp.name,
            "nif_titular_override": nif_titular_override,
            "import_id": import_id,
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
        _ejecutar_comercial_csv_job(
            job_id, tmp.name, nif_titular_override, import_id,
        ),
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "file_size_bytes": total_bytes,
        "import_id": import_id,
    }


async def _ejecutar_comercial_csv_job(
    job_id: str,
    tmp_path: str,
    nif_titular_override: Optional[str],
    import_id: str,
):
    """Worker background del import comercial. Espeja la lógica del endpoint
    síncrono pero con bulk_write y sin restricción de timeout HTTP."""
    import os

    try:
        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "status": "running",
                "started_at": _now_iso(),
                "updated_at": _now_iso(),
                "progress.phase": "reading",
            }},
        )

        with open(tmp_path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

        # Validación override
        override_info: Optional[dict] = None
        if nif_titular_override:
            nif_norm = nif_titular_override.strip().upper()
            catalogo = await _cargar_catalogo_sociedades()
            for _soc, info in catalogo.items():
                if info.get("nif_titular") == nif_norm:
                    override_info = {
                        "nif_titular": nif_norm,
                        "nombre_titular": info.get("nombre_titular") or "",
                    }
                    break
            if override_info is None:
                raise RuntimeError(
                    f"nif_titular_override={nif_norm!r} no está en el catálogo",
                )

        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {"progress.phase": "parsing", "updated_at": _now_iso()}},
        )

        origen_detectado = _detectar_formato_tabular(text)
        if origen_detectado:
            catalogo = await _cargar_catalogo_sociedades()
            registros, errores = _parsear_report_tabular(
                text, origen_detectado, catalogo_sociedades=catalogo,
            )
        else:
            registros, errores = _parsear_csv_generico(text)

        if override_info:
            for r in registros:
                r["nif_titular"] = override_info["nif_titular"]
                r["nombre_titular"] = override_info["nombre_titular"]
            errores = [
                e for e in errores
                if not (
                    e.get("fila") == -1
                    and "no encontradas en el catálogo" in e.get("motivo", "")
                )
            ]

        # Validación Pydantic + colecta de válidos
        validos: list[dict] = []
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
            validos.append(norm)

        total_validos = len(validos)
        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "progress.total": total_validos,
                "progress.phase": "inserting",
                "updated_at": _now_iso(),
            }},
        )

        # Bulk upsert por lotes de 2000 para tener progreso incremental
        batch_size = 2000
        insertadas = 0
        for i in range(0, total_validos, batch_size):
            chunk = validos[i : i + batch_size]
            await upsert_facturas_bulk(
                "facturas_comercial", chunk, "csv_comercial",
            )
            insertadas += len(chunk)
            await _db.jobs.update_one(
                {"id": job_id},
                {"$set": {
                    "progress.processed": insertadas,
                    "updated_at": _now_iso(),
                }},
            )

        # Match SII para el resumen
        nums = [r["num_serie_factura"] for r in registros if r.get("num_serie_factura")]
        matched_count = 0
        if nums:
            # Chunk para no rebasar 16MB de BSON en el $in
            for i in range(0, len(nums), 20_000):
                chunk = nums[i : i + 20_000]
                matched_count += await _db.facturas_sii.count_documents(
                    {"num_serie_factura": {"$in": chunk}}
                )

        if errores:
            await add_import_errors(_db, import_id, errores)

        await _db.jobs.update_one(
            {"id": job_id},
            {"$set": {
                "status": "done",
                "result": {
                    "total": insertadas,
                    "origen": origen_detectado,
                    "matches_sii": matched_count,
                    "sin_match_sii": max(0, len(nums) - matched_count),
                    "errores": errores[:50],
                    "import_id": import_id,
                },
                "finished_at": _now_iso(),
                "updated_at": _now_iso(),
                "progress.phase": "done",
            }},
        )
        await finish_import(
            _db, import_id, status="done",
            total_procesados=insertadas,
            insertados=insertadas,
            actualizados=0,
            extra={
                "origen_detectado": origen_detectado,
                "matches_sii": matched_count,
                "sin_match_sii": max(0, len(nums) - matched_count),
            },
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
        await finish_import(
            _db, import_id, status="error",
            error_message=f"{type(e).__name__}: {e}",
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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
        # Cabecera SAP FI clásica. Se distingue de SIGLO por `Doc.causante`
        # (nombre completo) frente a `Doc.caus.` (abreviado).
        "header_signatures": (
            "Soc.", "Doc.causante", "Tp.impos.", "BaseImpon", "Impto.ML",
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
        # Cabecera SIGLO. La distintiva es `Doc.caus.` (abreviado). Aceptamos
        # ambos nombres para el nº de factura porque algunos exports (p.ej.
        # HC30 de balance) traen `Nº doc.oficial` en lugar del `Nº oficial`
        # clásico, y otras columnas intermedias (Int.cial., Dat.adic., etc.)
        # no afectan al parser porque lo indexamos por nombre exacto de celda.
        "header_signatures": (
            "Soc.", "Doc.caus.", "Tp.impos.", "BaseImpon", "Impto.ML",
        ),
        "col_num":  ["Nº oficial", "Nº doc.oficial"],
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
    cuya firma de cabecera coincida (`SAP`, `SIGLO`...). None si ninguna.

    Compara TOKENS exactos (split por `|` + strip) en vez de substring: así
    `Doc.caus.` no matchea `Doc.causante` (que provocaría que SIGLO detectase
    ficheros SAP como SIGLO), y variantes con columnas extra (p.ej. HC30 con
    `Int.cial.`, `Dat.adic.`, `Cta.mayor`, `II`) siguen validando siempre que
    todas las columnas obligatorias estén presentes.
    """
    head = text.splitlines()[:100]
    for nombre, spec in _FORMATOS_TABULARES.items():
        sigs = spec["header_signatures"]
        for line in head:
            if not line.strip().startswith("|"):
                continue
            cells = {c.strip() for c in line.strip("|").split("|")}
            if all(sig in cells for sig in sigs):
                return nombre
    return None


_NIF_RE = re.compile(r"^[A-Z][0-9]{8}$")


def _es_nif(s: str) -> bool:
    """True si `s` tiene formato NIF (letra + 8 dígitos).

    En SAP FI, el campo `Soc.` viene ya con el NIF directamente
    (p.ej. `A74251836`) en lugar de un código de sociedad (p.ej. `HC30`).
    Usamos este check para decidir si podemos asignar `nif_titular` sin
    necesidad de pasar por el catálogo.
    """
    if not isinstance(s, str):
        return False
    return bool(_NIF_RE.match(s.strip().upper()))



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
        # Comparación por tokens exactos (mismo criterio que
        # `_detectar_formato_tabular`). Evita falsos positivos y garantiza
        # que el header_idx apunta a la fila real de cabecera.
        if not line.strip().startswith("|"):
            continue
        cells_set = {c.strip() for c in line.strip("|").split("|")}
        if all(sig in cells_set for sig in sigs):
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
    # Contadores de líneas saltadas por motivo — muy útil para auditar por
    # qué un fichero de N filas genera M<<N facturas. Se devuelven al final
    # como una entrada resumen en `errores` con fila=-1 para que el audit
    # trail los persista en `imports_log.errores`.
    skip_stats = {
        "no_pipe_prefix": 0,     # líneas que no empiezan por `|`
        "separator_line": 0,     # líneas `|---...|`
        "too_few_cells": 0,      # menos columnas que la cabecera
        "header_repeat": 0,      # reaparición de la cabecera
        "empty_num": 0,          # `num_serie_factura` vacío (típico en HC30)
        "parse_error": 0,        # excepciones al parsear la fila
    }
    # Ejemplos (máx 3 por motivo) de las líneas saltadas — para poder
    # inspeccionar visualmente qué tipo de línea es cada categoría.
    skip_samples: dict[str, list[str]] = {k: [] for k in skip_stats}

    def _record_skip(motivo: str, raw: str):
        skip_stats[motivo] += 1
        if len(skip_samples[motivo]) < 3:
            skip_samples[motivo].append(raw[:200])

    # Precomputamos el set de cells de la cabecera para detectar reapariciones
    # (algunos reports HC30 repiten la cabecera cada N líneas). También
    # detectamos líneas de título/subtotal que empiezan por `|` pero no son
    # filas de datos.
    header_cells_set = set(header_cells)
    for i, line in enumerate(lines[header_idx + 1 :], start=header_idx + 2):
        s = line.rstrip()
        if not s.startswith("|"):
            _record_skip("no_pipe_prefix", s)
            continue
        # Líneas separadoras estilo `|------...|`
        if set(s) <= {"|", "-", " "}:
            _record_skip("separator_line", s)
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < len(header_cells):
            _record_skip("too_few_cells", s)
            continue
        # Salta la reaparición de la cabecera dentro del cuerpo del report
        # (comparación exacta contra el conjunto de la cabecera detectada).
        # Sin esto los reports HC30 provocan una "Soc." fantasma en la lista
        # de sociedades no mapeadas y contaminan `errores`.
        if header_cells_set.issubset(set(cells)):
            _record_skip("header_repeat", s)
            continue
        num = cells[idx_num] if idx_num < len(cells) else ""
        if not num:
            _record_skip("empty_num", s)
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
            # Fallback: en SAP FI, `soc_origen` viene YA con el NIF de la
            # sociedad (formato letra + 8 dígitos, p.ej. "A74251836"). No hay
            # entrada en el catálogo porque el catálogo mapea CÓDIGOS
            # ("HC30", "HC39"...) a NIFs. Si el soc parece un NIF, lo
            # usamos directamente como `nif_titular`.
            resolved_nif = (mapping or {}).get("nif_titular")
            resolved_nombre = (mapping or {}).get("nombre_titular")
            if not resolved_nif and soc and _es_nif(soc):
                resolved_nif = soc.upper()
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
                    "nif_titular": resolved_nif,
                    "nombre_titular": resolved_nombre,
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
            _record_skip("parse_error", s)
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

    # Resumen de líneas saltadas — auditoría transparente. Se añade siempre
    # (incluso si todas son ceros) para que el usuario pueda comprobar en el
    # audit trail (`imports_log.errores`) cuántas líneas se descartaron y
    # por qué motivo. Sin esto, un HC30 de 1.7M filas que se agrega a 500k
    # facturas + genera sólo unos pocos errores puntuales deja al usuario
    # sin forma de verificar qué pasó con la ~1.2M restante.
    total_lineas_body = len(lines) - (header_idx + 1)
    total_saltadas = sum(skip_stats.values())
    lineas_datos_ok = total_lineas_body - total_saltadas
    errores.append({
        "fila": -1,
        "motivo": (
            f"[RESUMEN PARSEO {origen}] "
            f"total_lineas_cuerpo={total_lineas_body:,} · "
            f"lineas_datos_procesadas={lineas_datos_ok:,} · "
            f"lineas_saltadas={total_saltadas:,} "
            f"(no_pipe_prefix={skip_stats['no_pipe_prefix']:,}, "
            f"separator_line={skip_stats['separator_line']:,}, "
            f"too_few_cells={skip_stats['too_few_cells']:,}, "
            f"header_repeat={skip_stats['header_repeat']:,}, "
            f"empty_num={skip_stats['empty_num']:,}, "
            f"parse_error={skip_stats['parse_error']:,}) · "
            f"facturas_agregadas={len(registros_por_num):,}"
        ),
        "datos": {"skip_stats": skip_stats, "skip_samples": skip_samples},
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
        # iter27: si el único contenido del diff es la marca de reconciliación
        # por importe canónico, la factura se considera "coincide" (los diffs
        # de base/cuota/importe ya se retiraron en `diff_facturas`).
        real_diffs = {k: v for k, v in d.items() if not k.startswith("_")}
        reconciliada = d.get("_reconciliada_por_importe_canonico")
        estado = "coincide" if (not real_diffs) else "discrepancia"
        return {
            "num_serie_factura": ns,
            "estado": estado,
            "en_sii": True, "en_comercial": True,
            "diferencias": d, "sii": sii, "comercial": com,
            "reconciliada_por_importe_canonico": reconciliada is not None,
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
    tipos_factura: Optional[str] = None,
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

    Si `tipos_factura` (CSV: "F1,F2,R1,...") viene informado, se aplica al
    `tipo_factura` de SII. El comercial NO se filtra directamente porque su
    campo `tipo_factura` está vacío en todos los docs — la clasificación
    viene del SII cruzado por `num_serie_factura`. Los `solo_comercial` (sin
    SII match) se controlan aparte con el pseudo-código `_sin_clasificar`.
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
    # Filtro por tipo de factura (F1, F2, R1...). Refactor iter25:
    # `tipo_factura` ahora está denormalizado en `facturas_comercial`
    # (via backfill), por tanto se aplica DIRECTAMENTE en filtro_com
    # sin necesidad de $lookup con SII. `_sin_clasificar` == comercial
    # sin tipo (null/ausente/vacío).
    if tipos_factura:
        codes = [t.strip() for t in str(tipos_factura).split(",") if t.strip()]
        codes_real = [c for c in codes if c != "_sin_clasificar"]
        incluir_sin_clasif = "_sin_clasificar" in codes
        TODOS_TIPOS = {"F1", "F2", "F3", "F4", "R1", "R2", "R3", "R4", "R5"}
        # SII sólo tiene los tipos "reales" (nunca `_sin_clasificar`).
        if codes_real and set(codes_real) != TODOS_TIPOS:
            filtro_sii["tipo_factura"] = {"$in": codes_real}

        # Comercial: acepta docs con tipo en codes_real, más los sin
        # tipo (null/vacío) si _sin_clasificar está seleccionado.
        clauses_com: list[dict] = []
        if codes_real and set(codes_real) != TODOS_TIPOS:
            clauses_com.append({"tipo_factura": {"$in": codes_real}})
        elif codes_real:
            # todos los tipos reales marcados → clásula = tipo_factura ∈ TODOS
            clauses_com.append({"tipo_factura": {"$in": list(TODOS_TIPOS)}})
        if incluir_sin_clasif:
            clauses_com.append({"$or": [
                {"tipo_factura": None},
                {"tipo_factura": ""},
                {"tipo_factura": {"$exists": False}},
            ]})
        if len(clauses_com) > 1:
            filtro_com["$or"] = clauses_com
        elif len(clauses_com) == 1:
            # Fusionar la única clásula con el resto del filtro
            for k, v in clauses_com[0].items():
                filtro_com[k] = v
        else:
            # No hay nada seleccionado → resultado vacío por definición.
            filtro_com["_id"] = None
    if num_serie:
        regex_ns = {"$regex": re.escape(num_serie), "$options": "i"}
        filtro_sii["num_serie_factura"] = regex_ns
        filtro_com["num_serie_factura"] = regex_ns
    if nif_titular:
        nif_norm = str(nif_titular).strip().upper()
        filtro_sii["nif_titular"] = nif_norm
        # Sólo docs comerciales explícitamente etiquetados con este NIF.
        # Antes se incluía `None` (`$in: [nif, None]`) como compat legacy, pero
        # con dataset masivo sin backfill hacer eso arrastraba TODO el universo
        # de comerciales sin nif_titular (1M+ docs) al filtrar por cualquier
        # sociedad → OOM/500. Si el usuario tiene comerciales sin nif_titular
        # debe hacer backfill explícito antes de compararlas (endpoint
        # /api/admin/comercial/asignar-nif-titular-por-soc, o re-import con
        # `nif_titular_override`).
        filtro_com["nif_titular"] = nif_norm
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
    tipos_factura: Optional[str] = None,
):
    """Compara facturas SII vs Comercial por `num_serie_factura`.

    Filtros: `ejercicio`, `periodo`, `num_serie` (contiene), `estado`
    (coincide | discrepancia | solo_sii | solo_comercial), `nif_titular`,
    `tipos_factura` (CSV: "F1,F2,R1,...", con pseudo-código
    `_sin_clasificar` para las comerciales sin match SII).
    Paginación: `skip` / `limit` (default 50).

    Optimización: para evitar cargar millones de facturas SII en memoria,
    construimos los resultados desde el universo comercial (que siempre es
    pequeño) y sólo cargamos SII docs cuyo `num_serie` aparece en comercial.
    El estado `solo_sii` requiere escanear SII fuera del comercial y se
    pagina a nivel BD para no consumir memoria.

    Micro-cache (TTL=15s + single-flight): con 485k docs comerciales el
    procesamiento tarda 10-14s. Cuando el frontend dispara varias peticiones
    concurrentes (múltiples pestañas, refresh rápido, componentes hijos
    montándose en paralelo) las duplicadas se sirven de cache/aguardan al
    Future en vuelo, evitando saturar el pod y el 502 del ingress.
    """
    cache_key = (
        "comparativa",
        skip, limit, only_diffs, ejercicio, periodo, num_serie,
        estado, sort_by, sort_dir, nif_titular, tipos_factura,
    )
    return await _cached_or_compute(
        cache_key,
        lambda: _comparativa_impl(
            skip=skip, limit=limit, only_diffs=only_diffs,
            ejercicio=ejercicio, periodo=periodo, num_serie=num_serie,
            estado=estado, sort_by=sort_by, sort_dir=sort_dir,
            nif_titular=nif_titular, tipos_factura=tipos_factura,
        ),
    )


@router.get("/comparativa/bundle")
async def comparativa_bundle(
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
    tipos_factura: Optional[str] = None,
):
    """Endpoint agregado que devuelve las 3 vistas de la Comparativa en una
    sola petición: `list`, `totales`, `resumen_origenes`.

    ¿Por qué?
    Cuando el usuario cambia un filtro (mes, ejercicio, num_serie…), el
    frontend antiguo disparaba 3 peticiones EN PARALELO. Con un dataset
    grande (~1.4M docs concentrados en un mes), cada una tardaba 5-10s en
    frío y se saturaban entre ellas subiendo a 15-18s totales → el ingress
    devolvía 502.

    Este endpoint las ejecuta secuencialmente en el MISMO request; como
    cada sub-función tiene su propio cache, la 2ª/3ª son cache-hit
    instantáneas dentro de los 60s siguientes. Además, el frontend sólo
    dispara 1 conexión HTTP → cero paralelismo destructivo.

    Cacheado a su vez con la key del bundle para que refresh/pestañas
    repetidas sean instantáneas.
    """
    cache_key = (
        "bundle",
        skip, limit, only_diffs, ejercicio, periodo, num_serie,
        estado, sort_by, sort_dir, nif_titular, tipos_factura,
    )
    return await _cached_or_compute(
        cache_key,
        lambda: _comparativa_bundle_impl(
            skip=skip, limit=limit, only_diffs=only_diffs,
            ejercicio=ejercicio, periodo=periodo, num_serie=num_serie,
            estado=estado, sort_by=sort_by, sort_dir=sort_dir,
            nif_titular=nif_titular, tipos_factura=tipos_factura,
        ),
    )


async def _comparativa_bundle_impl(
    skip: int, limit: int, only_diffs: bool,
    ejercicio: Optional[str], periodo: Optional[str], num_serie: Optional[str],
    estado: Optional[str], sort_by: Optional[str], sort_dir: str,
    nif_titular: Optional[str], tipos_factura: Optional[str] = None,
):
    """Ejecuta las 3 sub-queries secuencialmente para que compartan cache y
    no compitan por CPU/event loop."""
    # Refactor 2026-02: totales y resumen usan ahora agregación nativa Mongo
    # ($group + $lookup) → sin cargar universos en RAM. Se retiran los
    # cortafuegos 200k/500k que rompían la UX al entrar sin NIF: la BD
    # aguanta 1.5M+ docs sin problema por estas 2 sub-queries.
    #
    # Además, ejecutamos las 3 sub-queries EN PARALELO con `asyncio.gather`
    # en lugar de secuencial. La aggregation con `$lookup` sobre 487k docs
    # tarda ~20s cada una — si las serializamos son 40-60s totales, en
    # paralelo se solapan y bajamos a ~20s (bound del más lento).
    import asyncio as _aio
    list_result, totales, resumen = await _aio.gather(
        _comparativa_impl(
            skip=skip, limit=limit, only_diffs=only_diffs,
            ejercicio=ejercicio, periodo=periodo, num_serie=num_serie,
            estado=estado, sort_by=sort_by, sort_dir=sort_dir,
            nif_titular=nif_titular, tipos_factura=tipos_factura,
        ),
        _comparativa_totales_impl(
            ejercicio=ejercicio, periodo=periodo,
            num_serie=num_serie, nif_titular=nif_titular,
            tipos_factura=tipos_factura,
        ),
        _comparativa_resumen_origenes_impl(
            ejercicio=ejercicio, periodo=periodo,
            num_serie=num_serie, nif_titular=nif_titular,
            tipos_factura=tipos_factura,
        ),
    )
    # Enriquecemos `totales.diferencias` con la métrica de conciliación
    # por Nº de facturas (matches / unión). Reutilizamos los `matches_sii`
    # que ya calculó `resumen_origenes` — evitamos un $lookup duplicado en
    # `_comparativa_totales_impl` (que suma 60-80s en datasets masivos).
    try:
        matches_total = sum(
            int(it.get("matches_sii") or 0)
            for it in (resumen.get("items") or [])
        )
        sii_n = int((totales.get("sii") or {}).get("n_facturas") or 0)
        com_n = int((totales.get("comercial_total") or {}).get("n_facturas") or 0)
        universo = sii_n + com_n - matches_total
        pct_fact = (
            round(matches_total / universo, 6) if universo > 0 else None
        )
        diff = totales.setdefault("diferencias", {})
        diff["matches_num_serie"] = matches_total
        diff["universo_num_serie"] = universo
        diff["pct_conciliado_facturas"] = pct_fact
    except Exception:  # noqa: BLE001
        pass
    return {
        "list": list_result,
        "totales": totales,
        "resumen_origenes": resumen,
    }


async def _comparativa_impl(
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
    tipos_factura: Optional[str] = None,
):
    """Implementación real de `/comparativa`. Separada del endpoint para
    poder envolverla con cache + single-flight sin duplicar la firma."""
    config = await _load_comparativa_config()
    filtro_sii, filtro_com = await _build_filtros(
        ejercicio, periodo, num_serie,
        excluir_base_cero=config["excluir_comercial_base_cero"],
        nif_titular=nif_titular,
        tipos_factura=tipos_factura,
    )
    # Parseamos `tipos_factura` para la lógica del bucket "_sin_clasificar"
    # (solo_comercial, que no tiene contraparte SII y por tanto tipo=null).
    tipos_set: set[str] = set()
    incluir_sin_clasificar = True
    if tipos_factura:
        _codes = [t.strip() for t in str(tipos_factura).split(",") if t.strip()]
        incluir_sin_clasificar = "_sin_clasificar" in _codes
        tipos_set = {c for c in _codes if c != "_sin_clasificar"}

    # Cortafuegos anti-OOM (relajado): sólo se aplica cuando el path elegido
    # necesita cargar el universo comercial en RAM para hacer diff en Python.
    # Los estados `solo_comercial` y `solo_sii` tienen fast-paths que paginan
    # directamente en Mongo (ver más abajo) → nunca golpean este límite y
    # deben pasar el guard incluso con 1.5M docs.
    #
    # Refactor 2026-02: el estado por defecto (diffs/all/coincide/discrepancia)
    # también usa ahora un fast-path por aggregation con $lookup cuando el
    # universo comercial supera 50k docs, por lo que el guard sólo dispara
    # cuando la vía legacy (Python) se activa con universos muy grandes.
    # Sin este guard, un usuario podría acabar consumiendo demasiada RAM si
    # deshabilita el fast-path por regex complejo o casos edge.
    if not nif_titular and estado not in ("solo_comercial", "solo_sii"):
        universo_com = await _db.facturas_comercial.count_documents(filtro_com)
        # Umbral generoso: el fast-path aggregation debería absorber todo
        # esto. Sólo bloqueamos si algo va MUY mal (>10M).
        if universo_com > 10_000_000:
            raise HTTPException(
                400,
                f"Dataset extremadamente grande sin filtro de sociedad "
                f"({universo_com:,} facturas comerciales). Selecciona una "
                f"sociedad concreta o filtra por ejercicio/periodo.",
            )

    # ------------------------------------------------------------------
    # FAST-PATH `estado=solo_comercial` (aggregation nativa Mongo).
    # Nota: los solo_comercial NO tienen contraparte SII → tampoco tienen
    # `tipo_factura`. Si el usuario filtra por tipos SIN marcar el
    # pseudo-código `_sin_clasificar`, no debemos mostrar ninguno.
    if estado == "solo_comercial" and tipos_factura and not incluir_sin_clasificar:
        return {
            "total": 0,
            "skip": skip,
            "limit": limit,
            "campos_canonicos": CAMPOS_CANONICOS,
            "campos_numericos": CAMPOS_NUMERICOS,
            "items": [],
        }

    # FAST-PATH `estado=solo_comercial` (aggregation nativa Mongo).
    #
    # Antes: cargábamos todos los `num_serie` de SII en un set Python
    # (~100 MB para 900k keys) y luego iterábamos comerciales en cursor.
    # Funcionaba pero rompía cuando el usuario hacía SORT o filtraba por
    # `num_serie` — se caía al legacy path que hace `to_list(None)` sobre
    # 1,5M comerciales → OOM inmediato del pod.
    #
    # Ahora: aggregation con `$lookup` inverso desde COMERCIAL a SII y
    # `$match` para quedarnos con los que NO tienen contraparte. Todo
    # paginado y ordenado en la BD → memoria constante. Soporta sort_by
    # y num_serie sin problemas.
    if estado == "solo_comercial":
        # Condiciones que un doc SII match debe cumplir para "descartar"
        # a este comercial. Referencian `$sii_raw` de la stage anterior.
        nif_norm = (
            str(nif_titular).strip().upper() if nif_titular else None
        )
        _plist = (
            [p.strip() for p in str(periodo).split(",") if p.strip()]
            if periodo else []
        )
        sii_extra_conds: list[dict] = []
        if nif_norm:
            sii_extra_conds.append({"$eq": ["$_sii_raw.nif_titular", nif_norm]})
        if ejercicio:
            sii_extra_conds.append({"$eq": ["$_sii_raw.ejercicio", str(ejercicio)]})
        if len(_plist) == 1:
            sii_extra_conds.append({"$eq": ["$_sii_raw.periodo", _plist[0]]})
        elif len(_plist) > 1:
            sii_extra_conds.append({"$in": ["$_sii_raw.periodo", _plist]})

        if sii_extra_conds:
            # Existe SII válido: array no vacío Y cumple los extra conds
            # (nif/ejercicio/periodo del filtro).
            has_valid_sii = {"$and": [
                {"$gt": [{"$size": "$_sii_docs"}, 0]},
                *sii_extra_conds,
            ]}
        else:
            # Existe SII (cualquier match por num_serie único): array no vacío.
            # Nota: usamos `$size` en lugar de `$ne: [null]` porque
            # `$arrayElemAt` de un array vacío devuelve `undefined` — no null —
            # y `$ne` no lo detecta como ausencia.
            has_valid_sii = {"$gt": [{"$size": "$_sii_docs"}, 0]}

        # Field para ordenar (a nivel BD). Solo permitimos campos del doc
        # comercial — no del SII, ya que estos docs no tienen SII match.
        SC_SORT_FIELD = {
            "num_serie_factura": "num_serie_factura",
            "fecha_expedicion": "fecha_expedicion",
            "importe_comercial": "importe_total",
            "estado": "num_serie_factura",  # no aplica, todos son solo_comercial
        }
        sc_sort = SC_SORT_FIELD.get(sort_by or "", "num_serie_factura")
        sc_dir = -1 if sort_dir == "desc" else 1

        sc_pipeline: list[dict] = [
            {"$match": filtro_com},
            {"$lookup": {
                "from": "facturas_sii",
                "localField": "num_serie_factura",
                "foreignField": "num_serie_factura",
                "as": "_sii_docs",
            }},
            {"$addFields": {
                "_sii_raw": {"$arrayElemAt": ["$_sii_docs", 0]},
            }},
            {"$addFields": {"_has_valid_sii": has_valid_sii}},
            {"$match": {"_has_valid_sii": False}},
            {"$facet": {
                "items": [
                    {"$sort": {sc_sort: sc_dir, "num_serie_factura": 1}},
                    {"$skip": skip},
                    {"$limit": limit},
                    {"$project": {
                        "_id": 0,
                        "_sii_docs": 0,
                        "_sii_raw": 0,
                        "_has_valid_sii": 0,
                        "versiones": 0,
                    }},
                ],
                "total": [{"$count": "n"}],
            }},
        ]
        sc_res = await _db.facturas_comercial.aggregate(
            sc_pipeline, allowDiskUse=True,
        ).to_list(length=1)
        if sc_res:
            facet = sc_res[0]
            page_docs = facet.get("items") or []
            total_arr = facet.get("total") or []
            total = int(
                (total_arr[0].get("n") if total_arr else 0) or 0
            )
        else:
            page_docs = []
            total = 0

        page_items = [
            _build_row_from_docs(
                None, d, d.get("num_serie_factura"), config,
            )
            for d in page_docs
        ]
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "campos_canonicos": CAMPOS_CANONICOS,
            "campos_numericos": CAMPOS_NUMERICOS,
            "items": page_items,
        }

    # FAST-PATH `estado=solo_sii` (aggregation nativa Mongo).
    #
    # Antes: pasaba por el legacy path que cargaba `com_docs` (1,5M) a RAM
    # con `to_list(length=None)` → OOM → datos parciales → estados
    # mezclados en el listado.
    #
    # Ahora: aggregation con `$lookup` inverso desde SII a comercial y
    # `$match` para quedarnos con los que NO tienen contraparte
    # comercial. Todo paginado y ordenado en BD. Simétrico al fast-path
    # de solo_comercial.
    if estado == "solo_sii":
        nif_norm = (
            str(nif_titular).strip().upper() if nif_titular else None
        )
        _plist = (
            [p.strip() for p in str(periodo).split(",") if p.strip()]
            if periodo else []
        )
        # Condiciones que un doc comercial match debe cumplir para
        # "descartar" a este SII (validación post-lookup del ámbito).
        com_extra_conds: list[dict] = []
        if nif_norm:
            com_extra_conds.append({"$eq": ["$$c.nif_titular", nif_norm]})
        if ejercicio:
            com_extra_conds.append({"$eq": ["$$c.ejercicio", str(ejercicio)]})
        if len(_plist) == 1:
            com_extra_conds.append({"$eq": ["$$c.periodo", _plist[0]]})
        elif len(_plist) > 1:
            com_extra_conds.append({"$in": ["$$c.periodo", _plist]})

        # ¿Existe algún doc comercial que cumpla las conds del ámbito?
        # Usamos `$map` + `$anyElementTrue` sobre `_com_docs`. Los
        # extra_conds están dentro del map ($$c referencia cada elem).
        if com_extra_conds:
            has_valid_com = {"$and": [
                {"$gt": [{"$size": "$_com_docs"}, 0]},
                {"$anyElementTrue": {
                    "$map": {
                        "input": "$_com_docs",
                        "as": "c",
                        "in": {"$and": com_extra_conds},
                    },
                }},
            ]}
        else:
            has_valid_com = {"$gt": [{"$size": "$_com_docs"}, 0]}

        SS_SORT_FIELD = {
            "num_serie_factura": "num_serie_factura",
            "fecha_expedicion": "fecha_expedicion",
            "importe_sii": "importe_total",
            "estado": "num_serie_factura",
        }
        ss_sort = SS_SORT_FIELD.get(sort_by or "", "num_serie_factura")
        ss_dir = -1 if sort_dir == "desc" else 1

        ss_pipeline: list[dict] = [
            {"$match": filtro_sii},
            {"$lookup": {
                "from": "facturas_comercial",
                "localField": "num_serie_factura",
                "foreignField": "num_serie_factura",
                "as": "_com_docs",
            }},
            {"$addFields": {"_has_valid_com": has_valid_com}},
            {"$match": {"_has_valid_com": False}},
            {"$facet": {
                "items": [
                    {"$sort": {ss_sort: ss_dir, "num_serie_factura": 1}},
                    {"$skip": skip},
                    {"$limit": limit},
                    {"$project": {
                        "_id": 0,
                        "_com_docs": 0,
                        "_has_valid_com": 0,
                        "versiones": 0,
                    }},
                ],
                "total": [{"$count": "n"}],
            }},
        ]
        ss_res = await _db.facturas_sii.aggregate(
            ss_pipeline, allowDiskUse=True,
        ).to_list(length=1)
        if ss_res:
            facet = ss_res[0]
            page_docs = facet.get("items") or []
            total_arr = facet.get("total") or []
            total = int(
                (total_arr[0].get("n") if total_arr else 0) or 0
            )
        else:
            page_docs = []
            total = 0

        page_items = [
            _build_row_from_docs(
                d, None, d.get("num_serie_factura"), config,
            )
            for d in page_docs
        ]
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "campos_canonicos": CAMPOS_CANONICOS,
            "campos_numericos": CAMPOS_NUMERICOS,
            "items": page_items,
        }

    # ------------------------------------------------------------------
    # FAST-PATH agregación con $lookup (dataset masivo)
    # ------------------------------------------------------------------
    # Cuando el universo comercial es grande (>50k docs), la vía legacy
    # (cargar toda `facturas_comercial` en RAM y hacer $in por chunks contra
    # SII) consume mucha memoria y tarda 20-30s. Usamos aggregation nativa
    # de Mongo con `$lookup` para paginar directamente en BD.
    #
    # Consideraciones:
    #  - `$lookup` usa el índice `num_serie_factura` de facturas_sii → rápido.
    #  - Comparamos `estado` a nivel cabecera (base + cuota + importe) con
    #    tolerancia 0.01 €. Es una aproximación suficiente para el listado;
    #    la diff detallada por campo se calcula en Python sobre la página
    #    devuelta (~50 filas) usando `diff_facturas` — igual que antes.
    #  - Aplica `invertir_signo_por_origen` al comparar cabecera.
    #  - Soporta estados: coincide, discrepancia, diffs (=only_diffs), all.
    #    (solo_comercial y solo_sii tienen fast-paths propios más arriba/abajo.)
    #  - `num_serie` regex se aplica en el `$match` inicial.
    #
    # Cuando el estado es 'solo_sii', no se aplica este fast-path: se cae
    # en el bloque legacy (solo_sii tiene su propio fast-path DB al final).
    universo_com_precheck = None
    if estado in (None, "diffs", "coincide", "discrepancia", "all") and estado != "solo_sii":
        universo_com_precheck = await _db.facturas_comercial.count_documents(filtro_com)

    if (
        universo_com_precheck is not None
        and universo_com_precheck > 50_000
        and estado != "solo_sii"
    ):
        cfg = config
        inv_map = cfg.get("invertir_signo_por_origen") or {}
        # Campos que definen "coincide" — configurable por el usuario en
        # /admin/comparativa. Por defecto: base_imponible + cuota_repercutida.
        campos_comparados = list(cfg.get("campos_comparados") or [
            "base_imponible", "cuota_repercutida",
        ])
        # Filtros que un doc SII match debe cumplir (para descartar cross-nif
        # o cross-periodo cuando el usuario acota el ámbito).
        # Refactor iter26: los filtros por ejercicio/periodo ya se aplican
        # en `filtro_com` directamente (los campos existen en comercial).
        # Por tanto no necesitamos condiciones adicionales sobre el SII.

        # Expresión "coincide a nivel cabecera" con inversión por origen
        # Construimos $switch por cada origen con inversión activa. Para el
        # resto de orígenes usamos comparación directa.
        origenes_invertidos = [k for k, v in inv_map.items() if v]

        SNAPSHOT_MAP = {
            "base_imponible": "_sii_base",
            "cuota_repercutida": "_sii_cuota",
            "importe_total": "_sii_importe_total",
        }
        # iter28.2: mapping del campo comercial al valor "neto" (post
        # exclusión de líneas tipo_impositivo=0/null). Los campos "_neto"
        # se calculan en un $addFields aguas arriba. Sin esto, el
        # aggregation marcaba "discrepancia" en facturas donde Python
        # `diff_facturas` (con exclusión aplicada) marca "coincide".
        COM_NETO_MAP = {
            "base_imponible": "_com_base_neto",
            "cuota_repercutida": "_com_cuota_neto",
            # importe_total: comparación directa (sin exclusión)
        }

        def _cmp_expr(sii_field: str, base_field_com: str):
            """Devuelve una expresión $switch que compara sii vs comercial.

            Refactor iter26: usa los campos snapshot `_sii_base`,
            `_sii_cuota`, `_sii_importe_total` denormalizados en el propio
            doc comercial. Sin `$lookup` → coste O(N) evitado.

            iter28.2: usa `_com_base_neto`/`_com_cuota_neto` (calculados
            con exclusión de líneas tipo_impositivo=0/null) en vez del
            campo raw, para alinear con la lógica de Python `diff_facturas`
            cuando `excluir_comercial_tipo_iva_cero=True`.
            """
            sii_snapshot = SNAPSHOT_MAP.get(sii_field, f"_sii_{sii_field}")
            com_field = COM_NETO_MAP.get(base_field_com, base_field_com)
            branches = []
            for og in origenes_invertidos:
                branches.append({
                    "case": {"$eq": ["$origen_comercial", og]},
                    "then": {"$lte": [
                        {"$abs": {"$add": [
                            {"$ifNull": [f"${sii_snapshot}", 0]},
                            {"$ifNull": [f"${com_field}", 0]},
                        ]}},
                        0.01,
                    ]},
                })
            direct_cmp = {"$lte": [
                {"$abs": {"$subtract": [
                    {"$ifNull": [f"${sii_snapshot}", 0]},
                    {"$ifNull": [f"${com_field}", 0]},
                ]}},
                0.01,
            ]}
            if branches:
                return {"$switch": {"branches": branches, "default": direct_cmp}}
            return direct_cmp

        # iter27: expresión "coincide por importe canónico" (fallback).
        # Cuando SII o Comercial no tienen desglose base/cuota pero sí
        # importe_total (típico en facturas No Sujeta), la comparación
        # campo a campo falla — pero la conciliación real cuadra por el
        # importe canónico = (base + cuota) if != 0 else importe_total.
        # iter28: prioridad importe_total > base+cuota.
        canonical_sii_expr = {
            "$let": {
                "vars": {
                    "importe": {"$ifNull": ["$_sii_importe_total", 0]},
                },
                "in": {
                    "$cond": [
                        {"$gt": [{"$abs": "$$importe"}, 0.01]},
                        "$$importe",
                        {"$add": [
                            {"$ifNull": ["$_sii_base", 0]},
                            {"$ifNull": ["$_sii_cuota", 0]},
                        ]},
                    ],
                },
            },
        }
        canonical_com_expr = {
            "$let": {
                "vars": {
                    "importe": {"$ifNull": ["$importe_total", 0]},
                },
                "in": {
                    "$cond": [
                        {"$gt": [{"$abs": "$$importe"}, 0.01]},
                        "$$importe",
                        {"$add": [
                            {"$ifNull": ["$base_imponible", 0]},
                            {"$ifNull": ["$cuota_repercutida", 0]},
                        ]},
                    ],
                },
            },
        }
        coincide_canonical_direct = {"$lte": [
            {"$abs": {"$subtract": [canonical_sii_expr, canonical_com_expr]}},
            0.01,
        ]}
        if origenes_invertidos:
            _inv_branches = [
                {"case": {"$eq": ["$origen_comercial", og]},
                 "then": {"$lte": [
                     {"$abs": {"$add": [canonical_sii_expr, canonical_com_expr]}},
                     0.01,
                 ]}}
                for og in origenes_invertidos
            ]
            coincide_canonical = {"$switch": {
                "branches": _inv_branches,
                "default": coincide_canonical_direct,
            }}
        else:
            coincide_canonical = coincide_canonical_direct

        # iter28.2: `_com_base_neto` y `_com_cuota_neto` — recalculan
        # base/cuota del comercial EXCLUYENDO líneas con tipo_impositivo
        # null/0 (misma lógica que Python `diff_facturas` con la config
        # `excluir_comercial_tipo_iva_cero=True`). Sin esto, aggregation
        # y Python discrepan y el filtro `only_diffs=true` incluye filas
        # que luego la UI muestra como "Coincide".
        excluir_tipo_cero = bool(cfg.get("excluir_comercial_tipo_iva_cero", True))
        if excluir_tipo_cero:
            _det_ok_cond = {
                "$and": [
                    {"$ne": [{"$ifNull": ["$$this.tipo_impositivo", None]}, None]},
                    {"$ne": [{"$toDouble": {"$ifNull": ["$$this.tipo_impositivo", 0]}}, 0]},
                ]
            }
            _sum_det_field_neto = lambda campo: {"$reduce": {
                "input": {"$ifNull": ["$detalle_iva", []]},
                "initialValue": 0.0,
                "in": {"$add": [
                    "$$value",
                    {"$cond": [
                        _det_ok_cond,
                        {"$toDouble": {"$ifNull": [f"$$this.{campo}", 0]}},
                        0.0,
                    ]},
                ]},
            }}
            com_base_neto_expr = {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    _sum_det_field_neto("base_imponible"),
                    {"$toDouble": {"$ifNull": ["$base_imponible", 0]}},
                ],
            }
            com_cuota_neto_expr = {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    _sum_det_field_neto("cuota_repercutida"),
                    {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}},
                ],
            }
        else:
            com_base_neto_expr = {"$toDouble": {"$ifNull": ["$base_imponible", 0]}}
            com_cuota_neto_expr = {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}}

        # Refactor iter26: pipeline sin `$lookup` masivo. Usamos el snapshot
        # `_has_sii` (denormalizado por el backfill). El `$lookup` diferido
        # se aplica DESPUÉS del `$skip`/`$limit` — máximo 50 filas.
        pipeline: list[dict] = [
            {"$match": filtro_com},
            {"$addFields": {
                # Normaliza `_has_sii` (algunos docs pre-backfill pueden
                # no tenerlo). Ausente → False (solo_comercial).
                "_has_sii_bool": {"$eq": [{"$ifNull": ["$_has_sii", False]}, True]},
                # iter28.2: valores comerciales POST-exclusión tipo_iva=0.
                "_com_base_neto": com_base_neto_expr,
                "_com_cuota_neto": com_cuota_neto_expr,
            }},
            {"$addFields": {
                "_coincide_header": {
                    "$cond": [
                        "$_has_sii_bool",
                        # Comparación campo a campo O por importe canónico
                        # (fallback iter27 para No Sujeta y desgloses asimétricos)
                        {"$or": [
                            {"$and": [_cmp_expr(f, f) for f in campos_comparados]},
                            coincide_canonical,
                        ]},
                        False,
                    ],
                },
            }},
            {"$addFields": {
                "_estado": {
                    "$cond": [
                        {"$not": "$_has_sii_bool"},
                        "solo_comercial",
                        {"$cond": [
                            "$_coincide_header",
                            "coincide",
                            "discrepancia",
                        ]},
                    ],
                },
            }},
        ]

        # Filtro por estado deseado
        if estado in ("coincide", "discrepancia", "solo_comercial"):
            pipeline.append({"$match": {"_estado": estado}})
        elif only_diffs:
            pipeline.append({"$match": {"_estado": {"$ne": "coincide"}}})
        # (all: sin filtro adicional, incluimos las 3 categorías desde comercial)

        # Filtro por tipos_factura ya aplicado en `filtro_com` (iter25).

        # Sort
        SORT_DB_FIELD = {
            "num_serie_factura": "num_serie_factura",
            "fecha_expedicion": "fecha_expedicion",
            "importe_sii": "_sii_importe_total",
            "importe_comercial": "importe_total",
            "estado": "_estado",
        }
        sort_field = SORT_DB_FIELD.get(sort_by or "num_serie_factura", "num_serie_factura")
        direction = -1 if sort_dir == "desc" else 1

        # $facet: total (barato: $count sobre pipeline filtrado) + página
        # (con $lookup diferido para leer el doc SII completo — pero SÓLO
        # sobre los ≤limit=50 docs de la página).
        pipeline.append({"$facet": {
            "items": [
                {"$sort": {sort_field: direction, "num_serie_factura": 1}},
                {"$skip": skip},
                {"$limit": limit},
                # Lookup DIFERIDO: sólo para los ≤50 docs de la página.
                # Necesario para el diff detallado en Python (diff_facturas
                # requiere el doc SII entero: detalle_iva, contraparte, etc.).
                {"$lookup": {
                    "from": "facturas_sii",
                    "localField": "num_serie_factura",
                    "foreignField": "num_serie_factura",
                    "as": "_sii_docs",
                }},
                {"$addFields": {
                    "_sii": {"$arrayElemAt": ["$_sii_docs", 0]},
                }},
                {"$project": {
                    "_id": 0,
                    "_sii_docs": 0,
                    "_has_sii_bool": 0,
                    "_sii._id": 0,
                    "_sii.versiones": 0,
                    "versiones": 0,
                }},
            ],
            "total": [{"$count": "n"}],
        }})

        agg_res = await _db.facturas_comercial.aggregate(
            pipeline, allowDiskUse=True,
        ).to_list(length=1)
        if agg_res:
            facet = agg_res[0]
            page_docs = facet.get("items") or []
            total_arr = facet.get("total") or []
            total = int((total_arr[0].get("n") if total_arr else 0) or 0)
        else:
            page_docs = []
            total = 0

        # Para el modo "all", el total real incluye también solo_sii (que
        # este pipeline no cuenta porque parte del universo comercial).
        # Sumamos el count aparte y avisamos al usuario en el UI.
        if estado is None and not only_diffs:
            solo_sii_filter_ct = dict(filtro_sii)
            # Excluimos los que existen en comercial → equivale a "no match".
            # Como estamos en universo grande usamos count total SII menos
            # matches SII estimados: aggregation con $lookup inverso.
            # Por simplicidad y coste, no lo sumamos (mismo comportamiento
            # que la vía legacy con `incluir_solo_sii=False`).
            _ = solo_sii_filter_ct

        # Aplica diff_facturas en Python sobre la página (max limit docs) para
        # calcular las diferencias campo a campo (necesarias para el
        # componente de detalle en el UI).
        items = []
        for d in page_docs:
            sii = d.pop("_sii", None) or None
            d.pop("_has_sii", None)
            d.pop("_coincide_header", None)
            estado_db = d.pop("_estado", None)
            ns = d.get("num_serie_factura")
            row = _build_row_from_docs(sii, d, ns, config)
            # Si Mongo dijo "coincide" pero Python encuentra tramos con
            # diferencias, el estado real es discrepancia. Sobrescribimos.
            # Esto puede pasar cuando cabecera coincide pero el detalle_iva
            # difiere → mantenemos consistencia con la lógica canónica.
            if estado_db == "coincide" and row.get("estado") == "discrepancia":
                # Reasignamos estado_db para reflejar la verdad
                pass  # row ya tiene "discrepancia" desde _build_row_from_docs
            items.append(row)

        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "campos_canonicos": CAMPOS_CANONICOS,
            "campos_numericos": CAMPOS_NUMERICOS,
            "items": items,
        }

    # ------------------------------------------------------------------
    # LEGACY PATH (universo pequeño, <= 50k docs): mantiene la lógica
    # anterior de cross-referencing en Python con Python diff exhaustivo
    # sobre todos los docs. Rápido con datasets pequeños y garantiza
    # semántica idéntica a la implementación original.
    # ------------------------------------------------------------------

    # 1) Universo comercial completo en scope (siempre pequeño)
    com_docs = await _db.facturas_comercial.find(
        filtro_com, {"_id": 0, "versiones": 0}
    ).to_list(length=None)
    com_map = {d["num_serie_factura"]: d for d in com_docs}
    com_keys = list(com_map.keys())

    # 2) Matches SII por num_serie ∈ comercial. Chunking obligatorio: con
    #    1.5M+ keys, un único `$in` supera 16MB de BSON (`DocumentTooLarge`)
    #    porque las keys viajan literales en el pipeline. Troceamos en
    #    chunks de 20 000 y unimos resultados en `sii_match_map`.
    NS_CHUNK = 20_000
    sii_match_map: dict[str, dict] = {}
    if com_keys:
        for i in range(0, len(com_keys), NS_CHUNK):
            chunk = com_keys[i : i + NS_CHUNK]
            partial = await _db.facturas_sii.find(
                {**filtro_sii, "num_serie_factura": {"$in": chunk}},
                {"_id": 0, "versiones": 0},
            ).to_list(length=None)
            for d in partial:
                sii_match_map[d["num_serie_factura"]] = d

    # 3) Filas de comercial: cada una será coincide / discrepancia / solo_comercial
    filas_com: list[dict] = []
    for ns, com in com_map.items():
        sii = sii_match_map.get(ns)
        filas_com.append(_build_row_from_docs(sii, com, ns, config))

    # 4) Contar SII fuera del comercial → estado solo_sii
    #    OJO: hay que preservar otros operadores que ya tenga filtro_sii sobre
    #    `num_serie_factura` (p.ej. el $regex de búsqueda del usuario). Mongo
    #    permite combinar $regex + $nin en el mismo subdocumento.
    #
    #    ATENCIÓN: `$nin` con >50k keys se acerca al límite BSON 16MB.
    #    Cuando el universo comercial es grande, usamos un fast-path
    #    aggregation con `$lookup` inverso (desde SII a comercial) y
    #    filtramos las que no tienen match.
    solo_sii_large_dataset = len(com_keys) > 50_000
    if solo_sii_large_dataset:
        # SII total en el universo del filtro, MENOS los que están en comercial.
        sii_total_universo = await _db.facturas_sii.count_documents(filtro_sii)
        solo_sii_total = max(0, sii_total_universo - len(sii_match_map))
        # `solo_sii_filter` no aplica aquí (no podemos meter $nin gigante).
        # Cuando pagemos solo_sii usaremos un pipeline agregado con $lookup.
        solo_sii_filter = None
    else:
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
        direction = -1 if sort_dir == "desc" else 1
        if solo_sii_large_dataset:
            # Fast-path aggregation: `$lookup` inverso desde SII a comercial,
            # filtramos los que NO tienen contraparte. Escala con datasets
            # de millones de docs sin cargar keys en memoria del proceso.
            sii_pipeline = [
                {"$match": filtro_sii},
                {"$lookup": {
                    "from": "facturas_comercial",
                    "localField": "num_serie_factura",
                    "foreignField": "num_serie_factura",
                    "as": "_com_docs",
                }},
            ]
            # Filtrar los que no tienen match en comercial (considerando el
            # filtro comercial: nif, ejercicio, periodo, base≠0). Un doc SII
            # está en "solo_sii" si NINGÚN doc comercial dentro del ámbito
            # comparte su num_serie_factura.
            com_match_conds = []
            if nif_titular:
                com_match_conds.append({"$eq": ["$$c.nif_titular", str(nif_titular).strip().upper()]})
            if ejercicio:
                com_match_conds.append({"$eq": ["$$c.ejercicio", str(ejercicio)]})
            if periodo:
                _plist = [p.strip() for p in str(periodo).split(",") if p.strip()]
                if len(_plist) == 1:
                    com_match_conds.append({"$eq": ["$$c.periodo", _plist[0]]})
                elif len(_plist) > 1:
                    com_match_conds.append({"$in": ["$$c.periodo", _plist]})
            # Un doc "coincide" (tiene match en comercial) si existe algún
            # elemento de _com_docs que cumple TODAS las com_match_conds.
            if com_match_conds:
                has_match_expr = {
                    "$anyElementTrue": {
                        "$map": {
                            "input": "$_com_docs",
                            "as": "c",
                            "in": {"$and": com_match_conds},
                        },
                    },
                }
            else:
                has_match_expr = {"$gt": [{"$size": "$_com_docs"}, 0]}
            sii_pipeline += [
                {"$addFields": {"_has_com": has_match_expr}},
                {"$match": {"_has_com": False}},
                {"$sort": {db_field: direction, "num_serie_factura": 1}},
                {"$skip": skip},
                {"$limit": limit},
                {"$project": {"_id": 0, "_com_docs": 0, "_has_com": 0, "versiones": 0}},
            ]
            sii_pagina = await _db.facturas_sii.aggregate(
                sii_pipeline, allowDiskUse=True,
            ).to_list(length=limit)
        else:
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

        # ------------------------------------------------------------------
        # Inclusión de filas "solo_sii" en los modos donde son relevantes
        # ------------------------------------------------------------------
        # `filas_com` se construye desde el universo comercial, así que NUNCA
        # contiene filas con estado=solo_sii. Eso provocaba un bug confuso:
        # al buscar por nº de serie específico, "Todas las facturas" o
        # "Discrepancias" decían "no encontrado" si la factura solo existe en
        # SII, mientras que "Sólo en SII" sí la encontraba.
        #
        # Solución: cuando hay un filtro restrictivo (`num_serie`), el
        # universo solo_sii ya es pequeño y podemos cargarlo en memoria sin
        # riesgo de OOM. Sin `num_serie`, mantenemos la optimización
        # (paginar solo_sii sólo cuando el usuario lo pida explícitamente con
        # estado="solo_sii") para no traer 800 000+ docs.
        incluir_solo_sii = bool(estado is None and num_serie)
        if incluir_solo_sii and solo_sii_filter is not None:
            # Con num_serie el universo comercial también es pequeño → cae en
            # la rama `else` que sí define solo_sii_filter. En modo large sin
            # num_serie no entramos aquí.
            solo_sii_docs = await _db.facturas_sii.find(
                solo_sii_filter, {"_id": 0, "versiones": 0}
            ).to_list(length=None)
            for d in solo_sii_docs:
                filas.append(
                    _build_row_from_docs(d, None, d["num_serie_factura"], config),
                )

        sort_func = _sort_key_row(sort_by or "num_serie_factura")
        filas.sort(key=sort_func, reverse=(sort_dir == "desc"))
        # Total/items:
        #   - Si hemos incluido solo_sii en filas → total = len(filas) (real).
        #   - Si no, y modo es "all" sin filtro restrictivo, sumamos al total
        #     el contador solo_sii pero los docs no aparecen en items (hay
        #     que cambiar a estado="solo_sii" para verlos). Avisamos en el UI.
        if incluir_solo_sii or estado or only_diffs:
            total = len(filas)
        else:
            total = len(filas) + solo_sii_total
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

    Cacheado con TTL=15s + single-flight igual que `/comparativa`.
    """
    cache_key = ("totales", ejercicio, periodo, num_serie, nif_titular)
    return await _cached_or_compute(
        cache_key,
        lambda: _comparativa_totales_impl(
            ejercicio=ejercicio, periodo=periodo,
            num_serie=num_serie, nif_titular=nif_titular,
        ),
    )


async def _comparativa_totales_impl(
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    num_serie: Optional[str] = None,
    nif_titular: Optional[str] = None,
    tipos_factura: Optional[str] = None,
):
    """Totales por origen usando **aggregation pipeline** nativo Mongo.

    Refactor 2026-02: Antes iteraba con `find().async for` sobre 1.5M+ docs
    para sumar bases/cuotas → tardaba 30-60s y podía OOM-killar el pod.

    Ahora ejecuta `$group` en la BD y devuelve solo los totales (una fila
    por origen). MongoDB aprovecha el índice compuesto (nif_titular,
    ejercicio, periodo) para el `$match`, y el `$group` es streaming en el
    servidor Mongo → memoria constante independientemente del universo.

    Devuelve exactamente la misma estructura que la versión anterior para
    no romper el frontend (ResumenTotales.jsx).
    """
    config = await _load_comparativa_config()
    filtro_sii, filtro_com = await _build_filtros(
        ejercicio, periodo, num_serie,
        excluir_base_cero=config["excluir_comercial_base_cero"],
        nif_titular=nif_titular,
        tipos_factura=tipos_factura,
    )

    excluir_tipo_iva_cero = config.get("excluir_comercial_tipo_iva_cero", True)
    inv_map = config.get("invertir_signo_por_origen") or {}

    # ------------------------------------------------------------------
    # SII: totales agregados (sumamos detalle_iva si existe, cabecera si no)
    # ------------------------------------------------------------------
    # Estrategia: en el aggregation, si `detalle_iva` existe y no está vacío,
    # sumamos base/cuota del detalle; en caso contrario, usamos cabecera.
    #
    # iter27: Fallback por importe canónico. Cuando una factura SII no tiene
    # desglose (base=cuota=0) pero sí importe_total (típico No Sujeta con
    # clave_regimen_especial=08), tratamos `importe_total` como base para
    # que el KPI del resumen refleje el peso económico real de la factura.
    # Sin esto, el usuario ve "0 € en SII" para facturas No Sujeta y una
    # falsa Δ Base vs Comercial (que sí desglosa esas facturas).
    sii_pipeline = [
        {"$match": filtro_sii},
        {"$project": {
            "_id": 0,
            "fecha_expedicion": 1,
            "_base_raw": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    {"$reduce": {
                        "input": {"$ifNull": ["$detalle_iva", []]},
                        "initialValue": 0.0,
                        "in": {"$add": [
                            "$$value",
                            {"$toDouble": {"$ifNull": ["$$this.base_imponible", 0]}},
                        ]},
                    }},
                    {"$toDouble": {"$ifNull": ["$base_imponible", 0]}},
                ],
            },
            "_cuota_raw": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    {"$reduce": {
                        "input": {"$ifNull": ["$detalle_iva", []]},
                        "initialValue": 0.0,
                        "in": {"$add": [
                            "$$value",
                            {"$toDouble": {"$ifNull": ["$$this.cuota_repercutida", 0]}},
                        ]},
                    }},
                    {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}},
                ],
            },
            "_importe_total": {"$toDouble": {"$ifNull": ["$importe_total", 0]}},
        }},
        {"$addFields": {
            # iter27: si base+cuota ≈ 0 pero hay importe_total, éste va a base.
            "_base": {
                "$cond": [
                    {"$and": [
                        {"$lte": [{"$abs": {"$add": ["$_base_raw", "$_cuota_raw"]}}, 0.01]},
                        {"$gt": [{"$abs": "$_importe_total"}, 0.01]},
                    ]},
                    "$_importe_total",
                    "$_base_raw",
                ],
            },
            "_cuota": "$_cuota_raw",
        }},
        {"$group": {
            "_id": None,
            "base": {"$sum": "$_base"},
            "cuota": {"$sum": "$_cuota"},
            "n_facturas": {"$sum": 1},
            "ultima_fecha_expedicion": {"$max": "$fecha_expedicion"},
        }},
    ]
    sii_res = await _db.facturas_sii.aggregate(sii_pipeline).to_list(length=1)
    if sii_res:
        r = sii_res[0]
        sii_base = float(r.get("base") or 0)
        sii_cuota = float(r.get("cuota") or 0)
        sii_n = int(r.get("n_facturas") or 0)
        sii_ultima_fecha = r.get("ultima_fecha_expedicion")
    else:
        sii_base = 0.0
        sii_cuota = 0.0
        sii_n = 0
        sii_ultima_fecha = None

    # ------------------------------------------------------------------
    # Comercial: totales por origen (aplicando filtro de tipo_impositivo 0)
    # ------------------------------------------------------------------
    # Cuando `excluir_tipo_iva_cero=True` filtramos las líneas del detalle
    # con `tipo_impositivo` null o 0. A nivel cabecera (sin detalle) usamos
    # `$cond` para aportar 0 si el tipo_impositivo del doc es null/0.
    if excluir_tipo_iva_cero:
        det_base_expr = {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$cond": [
                    {"$and": [
                        {"$ne": [{"$ifNull": ["$$this.tipo_impositivo", None]}, None]},
                        {"$ne": [{"$toDouble": {"$ifNull": ["$$this.tipo_impositivo", 0]}}, 0]},
                    ]},
                    {"$toDouble": {"$ifNull": ["$$this.base_imponible", 0]}},
                    0.0,
                ]},
            ]},
        }}
        det_cuota_expr = {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$cond": [
                    {"$and": [
                        {"$ne": [{"$ifNull": ["$$this.tipo_impositivo", None]}, None]},
                        {"$ne": [{"$toDouble": {"$ifNull": ["$$this.tipo_impositivo", 0]}}, 0]},
                    ]},
                    {"$toDouble": {"$ifNull": ["$$this.cuota_repercutida", 0]}},
                    0.0,
                ]},
            ]},
        }}
        # Cabecera sin detalle: filtra por tipo_impositivo
        header_base_expr = {"$cond": [
            {"$and": [
                {"$ne": [{"$ifNull": ["$tipo_impositivo", None]}, None]},
                {"$ne": [{"$toDouble": {"$ifNull": ["$tipo_impositivo", 0]}}, 0]},
            ]},
            {"$toDouble": {"$ifNull": ["$base_imponible", 0]}},
            0.0,
        ]}
        header_cuota_expr = {"$cond": [
            {"$and": [
                {"$ne": [{"$ifNull": ["$tipo_impositivo", None]}, None]},
                {"$ne": [{"$toDouble": {"$ifNull": ["$tipo_impositivo", 0]}}, 0]},
            ]},
            {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}},
            0.0,
        ]}
    else:
        det_base_expr = {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$toDouble": {"$ifNull": ["$$this.base_imponible", 0]}},
            ]},
        }}
        det_cuota_expr = {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$toDouble": {"$ifNull": ["$$this.cuota_repercutida", 0]}},
            ]},
        }}
        header_base_expr = {"$toDouble": {"$ifNull": ["$base_imponible", 0]}}
        header_cuota_expr = {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}}

    # Parseamos tipos_factura para filtrar comerciales via SII match.
    # El campo `tipo_factura` NO existe en `facturas_comercial` → el filtro
    # se aplica indirectamente: comercial cuenta como tipo X si su match
    # SII (por num_serie único) es de tipo X. Los solo_comercial (sin
    # match SII) sólo cuentan si `_sin_clasificar` está seleccionado.
    # Refactor iter25: `tipo_factura` denormalizado en comercial.
    # `_build_filtros` ya aplica el filtro directamente en `filtro_com`
    # → sin necesidad de `$lookup` (antes tardaba 40-60s).
    com_pipeline: list[dict] = [
        {"$match": filtro_com},
        {"$project": {
            "_id": 0,
            "origen": {"$ifNull": ["$origen_comercial", "DESCONOCIDO"]},
            "fecha_expedicion": 1,
            "_base_raw": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    det_base_expr,
                    header_base_expr,
                ],
            },
            "_cuota_raw": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    det_cuota_expr,
                    header_cuota_expr,
                ],
            },
            "_importe_total": {"$toDouble": {"$ifNull": ["$importe_total", 0]}},
        }},
        {"$addFields": {
            # iter27: mismo fallback canónico que SII — importe_total va a
            # base si el desglose está a 0.
            "_base": {
                "$cond": [
                    {"$and": [
                        {"$lte": [{"$abs": {"$add": ["$_base_raw", "$_cuota_raw"]}}, 0.01]},
                        {"$gt": [{"$abs": "$_importe_total"}, 0.01]},
                    ]},
                    "$_importe_total",
                    "$_base_raw",
                ],
            },
            "_cuota": "$_cuota_raw",
        }},
        {"$group": {
            "_id": "$origen",
            "base": {"$sum": "$_base"},
            "cuota": {"$sum": "$_cuota"},
            "n_facturas": {"$sum": 1},
            "ultima_fecha_expedicion": {"$max": "$fecha_expedicion"},
        }},
    ]
    com_res = await _db.facturas_comercial.aggregate(
        com_pipeline, allowDiskUse=True,
    ).to_list(length=None)

    por_origen: dict[str, dict] = {}
    for r in com_res:
        origen = r["_id"] or "DESCONOCIDO"
        base = float(r.get("base") or 0)
        cuota = float(r.get("cuota") or 0)
        invertido = bool(inv_map.get(origen))
        if invertido:
            base = -base
            cuota = -cuota
        por_origen[origen] = {
            "base": base,
            "cuota": cuota,
            "n_facturas": int(r.get("n_facturas") or 0),
            "invertido": invertido,
            "ultima_fecha_expedicion": r.get("ultima_fecha_expedicion"),
        }

    com_base = sum(o["base"] for o in por_origen.values())
    com_cuota = sum(o["cuota"] for o in por_origen.values())
    com_n = sum(o["n_facturas"] for o in por_origen.values())

    # Nota: matches_num_serie / pct_conciliado_facturas NO se calcula aquí
    # para evitar un $lookup adicional (60-80s en datasets grandes). Se
    # rellena desde el bundle sumando `matches_sii` del resumen_origenes
    # (que ya hace ese $lookup). Cuando este endpoint se llama sin bundle,
    # el frontend puede consultarlo por separado en /comparativa/resumen-origenes.
    matches_num_serie: int | None = None
    universo_facturas: int | None = None
    pct_conciliado_facturas: float | None = None

    diff_base = round(sii_base - com_base, 2)
    diff_cuota = round(sii_cuota - com_cuota, 2)
    # iter27: importe canónico total = base + cuota. Cuando hay desglose
    # asimétrico (No Sujeta, etc.) esta suma es la que refleja la realidad
    # económica y el Δ canónico ≈ 0 aunque los Δ base/cuota individuales
    # sean != 0.
    sii_canonico = round(sii_base + sii_cuota, 2)
    com_canonico = round(com_base + com_cuota, 2)
    diff_canonico = round(sii_canonico - com_canonico, 2)

    def _pct(num: float, denom: float) -> float | None:
        if denom == 0:
            return None
        return round(1.0 - abs(num) / abs(denom), 6)

    return {
        "sii": {
            "base": round(sii_base, 2),
            "cuota": round(sii_cuota, 2),
            "canonico": sii_canonico,
            "n_facturas": sii_n,
            "ultima_fecha_expedicion": sii_ultima_fecha,
        },
        "comercial_por_origen": {
            k: {
                "base": round(v["base"], 2),
                "cuota": round(v["cuota"], 2),
                "canonico": round(v["base"] + v["cuota"], 2),
                "n_facturas": v["n_facturas"],
                "invertido": v["invertido"],
                "ultima_fecha_expedicion": v["ultima_fecha_expedicion"],
            }
            for k, v in sorted(por_origen.items())
        },
        "comercial_total": {
            "base": round(com_base, 2),
            "cuota": round(com_cuota, 2),
            "canonico": com_canonico,
            "n_facturas": com_n,
        },
        "diferencias": {
            "base": diff_base,
            "cuota": diff_cuota,
            "canonico": diff_canonico,
            "pct_conciliado_base": _pct(diff_base, sii_base),
            "pct_conciliado_cuota": _pct(diff_cuota, sii_cuota),
            "pct_conciliado_canonico": _pct(diff_canonico, sii_canonico),
            # Nueva métrica: % conciliado por NÚMERO de facturas.
            # matches / (union SII ∪ Comercial por num_serie).
            "matches_num_serie": matches_num_serie,
            "universo_num_serie": universo_facturas,
            "pct_conciliado_facturas": pct_conciliado_facturas,
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

    Cacheado con TTL=15s + single-flight (aggregate tarda ~2s con 485k docs).
    """
    cache_key = ("periodos", nif_titular)
    return await _cached_or_compute(
        cache_key,
        lambda: _comparativa_periodos_impl(nif_titular=nif_titular),
    )


async def _comparativa_periodos_impl(nif_titular: Optional[str] = None):
    sii_match: dict = {}
    com_match: dict = {}
    if nif_titular:
        nif_norm = str(nif_titular).strip().upper()
        sii_match["nif_titular"] = nif_norm
        # Consistente con _build_filtros: sólo docs etiquetados explícitamente.
        com_match["nif_titular"] = nif_norm

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

    Cacheado con TTL=15s + single-flight igual que `/comparativa`.
    """
    cache_key = ("resumen-origenes", ejercicio, periodo, num_serie, nif_titular)
    return await _cached_or_compute(
        cache_key,
        lambda: _comparativa_resumen_origenes_impl(
            ejercicio=ejercicio, periodo=periodo,
            num_serie=num_serie, nif_titular=nif_titular,
        ),
    )


async def _comparativa_resumen_origenes_impl(
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    num_serie: Optional[str] = None,
    nif_titular: Optional[str] = None,
    tipos_factura: Optional[str] = None,
):
    """Resumen agregado por `origen_comercial` con **aggregation nativa**.

    Refactor 2026-02: antes iteraba con cursor + batches de 20k `$in`
    contra SII para contar matches → costoso con 1M+ docs. Ahora usa un
    solo `$lookup` en el aggregation pipeline: MongoDB aprovecha el índice
    en `facturas_sii.num_serie_factura` para hacer el join fila a fila
    sin traer nada al backend.

    Además, con `$lookup` la comparación coincide/discrepancia se hace a
    nivel cabecera (base + cuota + importe_total) directamente en el
    servidor Mongo — suficiente para KPIs de conciliación por origen.

    El resultado mantiene el mismo shape que la versión anterior.
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
        filtro_com["nif_titular"] = nif_norm
    else:
        nif_norm = None
    if config["excluir_comercial_base_cero"]:
        filtro_com["base_imponible"] = {"$nin": [0, 0.0, None]}

    inv_map = config.get("invertir_signo_por_origen") or {}
    campos_comparados = list(config.get("campos_comparados") or [
        "base_imponible", "cuota_repercutida",
    ])
    origenes_invertidos_resumen = [k for k, v in inv_map.items() if v]

    def _resumen_cmp_expr(field: str):
        """Expresión "coincide en `field`" respetando la inversión de signo.

        Refactor iter26: usa los campos snapshot `_sii_base`, `_sii_cuota`,
        `_sii_importe_total` directamente en el doc comercial (denormalizados
        por el backfill). Sin `$lookup` → sub-segundo en 1M+ docs.

        iter28.2: usa `_com_base_neto`/`_com_cuota_neto` (post exclusión
        tipo_impositivo=0/null) en vez del campo raw, para alinear con
        `diff_facturas` de Python.
        """
        # Mapeo de campos SII → snapshot correspondiente en el comercial
        SNAPSHOT_MAP = {
            "base_imponible": "_sii_base",
            "cuota_repercutida": "_sii_cuota",
            "importe_total": "_sii_importe_total",
        }
        COM_NETO_MAP = {
            "base_imponible": "_com_base_neto",
            "cuota_repercutida": "_com_cuota_neto",
        }
        sii_field = SNAPSHOT_MAP.get(field, f"_sii_{field}")
        com_field = COM_NETO_MAP.get(field, field)
        direct = {"$lte": [
            {"$abs": {"$subtract": [
                {"$ifNull": [f"${sii_field}", 0]},
                {"$ifNull": [f"${com_field}", 0]},
            ]}},
            0.01,
        ]}
        if not origenes_invertidos_resumen:
            return direct
        branches = [
            {"case": {"$eq": ["$origen_comercial", og]},
             "then": {"$lte": [
                 {"$abs": {"$add": [
                     {"$ifNull": [f"${sii_field}", 0]},
                     {"$ifNull": [f"${com_field}", 0]},
                 ]}},
                 0.01,
             ]}}
            for og in origenes_invertidos_resumen
        ]
        return {"$switch": {"branches": branches, "default": direct}}

    # iter27: expresión "coincide por importe canónico" — cubre casos
    # asimétricos como facturas No Sujeta (SII sólo tiene importe_total,
    # comercial desglosa en base+cuota).
    #
    # canonical_sii = _sii_base + _sii_cuota si != 0, si no _sii_importe_total
    # canonical_com = base + cuota          si != 0, si no importe_total
    # coincide_canonical = |canonical_sii - canonical_com| ≤ 0.01
    #                     (con inversión signo si origen invertido)
    # iter28: prioridad `importe_total` > `base+cuota`. Cubre facturas
    # con partes exentas/no sujetas donde importe_total ≠ base+cuota.
    canonical_sii_expr = {
        "$let": {
            "vars": {
                "importe": {"$ifNull": ["$_sii_importe_total", 0]},
            },
            "in": {
                "$cond": [
                    {"$gt": [{"$abs": "$$importe"}, 0.01]},
                    "$$importe",
                    {"$add": [
                        {"$ifNull": ["$_sii_base", 0]},
                        {"$ifNull": ["$_sii_cuota", 0]},
                    ]},
                ],
            },
        },
    }
    canonical_com_expr = {
        "$let": {
            "vars": {
                "importe": {"$ifNull": ["$importe_total", 0]},
            },
            "in": {
                "$cond": [
                    {"$gt": [{"$abs": "$$importe"}, 0.01]},
                    "$$importe",
                    {"$add": [
                        {"$ifNull": ["$base_imponible", 0]},
                        {"$ifNull": ["$cuota_repercutida", 0]},
                    ]},
                ],
            },
        },
    }
    # Comparación canonical con inversión: si origen invertido → suma;
    # si no → resta. Tolerancia 0.01.
    coincide_canonical_direct = {"$lte": [
        {"$abs": {"$subtract": [canonical_sii_expr, canonical_com_expr]}},
        0.01,
    ]}
    if origenes_invertidos_resumen:
        _inv_branches = [
            {"case": {"$eq": ["$origen_comercial", og]},
             "then": {"$lte": [
                 {"$abs": {"$add": [canonical_sii_expr, canonical_com_expr]}},
                 0.01,
             ]}}
            for og in origenes_invertidos_resumen
        ]
        coincide_canonical = {"$switch": {
            "branches": _inv_branches,
            "default": coincide_canonical_direct,
        }}
    else:
        coincide_canonical = coincide_canonical_direct

    # iter28.2: `_com_base_neto` y `_com_cuota_neto` — recalculan
    # base/cuota EXCLUYENDO líneas tipo_impositivo=0/null (misma lógica
    # que Python `diff_facturas`), para alinear con la UI.
    excluir_tipo_cero_r = bool(config.get("excluir_comercial_tipo_iva_cero", True))
    if excluir_tipo_cero_r:
        _det_ok_cond_r = {
            "$and": [
                {"$ne": [{"$ifNull": ["$$this.tipo_impositivo", None]}, None]},
                {"$ne": [{"$toDouble": {"$ifNull": ["$$this.tipo_impositivo", 0]}}, 0]},
            ]
        }
        _sum_det_neto_r = lambda c: {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$cond": [
                    _det_ok_cond_r,
                    {"$toDouble": {"$ifNull": [f"$$this.{c}", 0]}},
                    0.0,
                ]},
            ]},
        }}
        com_base_neto_expr_r = {
            "$cond": [
                {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                _sum_det_neto_r("base_imponible"),
                {"$toDouble": {"$ifNull": ["$base_imponible", 0]}},
            ],
        }
        com_cuota_neto_expr_r = {
            "$cond": [
                {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                _sum_det_neto_r("cuota_repercutida"),
                {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}},
            ],
        }
    else:
        com_base_neto_expr_r = {"$toDouble": {"$ifNull": ["$base_imponible", 0]}}
        com_cuota_neto_expr_r = {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}}

    # Refactor iter26: pipeline SIN `$lookup`. Todo el trabajo se hace
    # sobre `facturas_comercial` usando los campos snapshot denormalizados
    # por el backfill (`_has_sii`, `_sii_base`, `_sii_cuota`,
    # `_sii_importe_total`). Antes: 30-40s con 1M docs. Ahora: <1s.
    pipeline = [
        {"$match": filtro_com or {}},
        {"$addFields": {
            "_origen": {"$ifNull": ["$origen_comercial", "desconocido"]},
            "_com_base_neto": com_base_neto_expr_r,
            "_com_cuota_neto": com_cuota_neto_expr_r,
        }},
        {"$addFields": {
            "_coincide": {
                "$cond": [
                    {"$eq": [{"$ifNull": ["$_has_sii", False]}, True]},
                    # Coincidencia según `campos_comparados` configurados
                    # (respetando inversión de signo por origen). iter27:
                    # además, si el importe canónico cuadra, se considera
                    # coincide aunque los campos individuales no coincidan
                    # (cubre facturas No Sujeta / desglose asimétrico).
                    {"$or": [
                        {"$and": [_resumen_cmp_expr(f) for f in campos_comparados]},
                        coincide_canonical,
                    ]},
                    False,
                ],
            },
        }},
        {"$group": {
            "_id": "$_origen",
            "total_facturas": {"$sum": 1},
            "base_total": {"$sum": {"$ifNull": ["$base_imponible", 0]}},
            "cuota_total": {"$sum": {"$ifNull": ["$cuota_repercutida", 0]}},
            "importe_total": {"$sum": {"$ifNull": ["$importe_total", 0]}},
            "matches_sii": {"$sum": {
                "$cond": [
                    {"$eq": [{"$ifNull": ["$_has_sii", False]}, True]},
                    1, 0,
                ],
            }},
            "coincidencias": {"$sum": {"$cond": ["$_coincide", 1, 0]}},
        }},
        {"$sort": {"total_facturas": -1}},
    ]

    grupos = await _db.facturas_comercial.aggregate(
        pipeline, allowDiskUse=True,
    ).to_list(length=None)

    resultados = []
    for g in grupos:
        origen = g["_id"]
        total = int(g.get("total_facturas") or 0)
        matches = int(g.get("matches_sii") or 0)
        coincidencias = int(g.get("coincidencias") or 0)
        # Los orígenes invertidos ahora sí devuelven coincide/discrepancia
        # porque el aggregation aplica correctamente la inversión de signo
        # (ver `_resumen_cmp_expr`). No hay motivo para marcarlos como null.
        coincidencias_out = coincidencias
        discrepancias_out = matches - coincidencias
        resultados.append({
            "origen": origen,
            "total_facturas": total,
            "base_total": round(float(g.get("base_total") or 0), 2),
            "cuota_total": round(float(g.get("cuota_total") or 0), 2),
            "importe_total": round(float(g.get("importe_total") or 0), 2),
            "matches_sii": matches,
            "sin_match_sii": total - matches,
            "coincidencias": coincidencias_out,
            "discrepancias": discrepancias_out,
        })

    return {"items": resultados}


@router.get("/comparativa/nifs-titulares")
async def comparativa_nifs_titulares():
    """Devuelve la lista distinct de `nif_titular` presentes en SII y comercial,
    enriquecida con el `nombre_titular` desde el catálogo de sociedades.

    Útil para construir el toggle de "Sociedad" en la UI. Si en el comercial
    existen docs sin nif_titular (data legacy), se devuelve adicionalmente el
    contador `comercial_sin_nif` para que la UI pueda avisar al usuario.

    Cacheado con TTL=15s + single-flight.
    """
    cache_key = ("nifs-titulares",)
    return await _cached_or_compute(
        cache_key, _comparativa_nifs_titulares_impl,
    )


async def _comparativa_nifs_titulares_impl():
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
    # Volumen por sociedad (para que el frontend pueda elegir la más
    # pequeña como default y minimizar el tiempo de cache-miss inicial).
    volumen_pipe = [{"$group": {"_id": "$nif_titular", "n": {"$sum": 1}}}]
    vol_com = {
        r["_id"]: int(r.get("n") or 0)
        for r in await _db.facturas_comercial.aggregate(volumen_pipe).to_list(None)
        if r.get("_id")
    }
    vol_sii = {
        r["_id"]: int(r.get("n") or 0)
        for r in await _db.facturas_sii.aggregate(volumen_pipe).to_list(None)
        if r.get("_id")
    }
    sociedades = [
        {
            "nif_titular": n,
            "nombre_titular": nif_to_nombre.get(n, ""),
            "n_comercial": vol_com.get(n, 0),
            "n_sii": vol_sii.get(n, 0),
        }
        for n in nifs
    ]
    return {
        "nifs_titulares": nifs,
        "sociedades": sociedades,
        "comercial_sin_nif": comercial_sin_nif,
    }



@router.get("/comparativa/tipos-factura")
async def comparativa_tipos_factura(
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    nif_titular: Optional[str] = None,
):
    """Devuelve contadores por `tipo_factura` para poblar el filtro
    multi-select. Cada bucket contiene el nº de facturas SII (F1..F4/R1..R5)
    dentro del ámbito filtrado + un bucket `_sin_clasificar` con las
    comerciales sin match SII (por definición sin tipo conocido).
    """
    cache_key = ("tipos-factura", ejercicio, periodo, nif_titular)
    return await _cached_or_compute(
        cache_key,
        lambda: _comparativa_tipos_factura_impl(
            ejercicio=ejercicio, periodo=periodo, nif_titular=nif_titular,
        ),
    )


# Catálogo canónico de tipos de factura AEAT (XSD SuministroInformacion).
_TIPOS_FACTURA_CATALOG = [
    ("F1", "Factura normal", "normal"),
    ("F2", "Simplificada / tique", "normal"),
    ("F3", "Reemplaza simplificada", "normal"),
    ("F4", "Resumen de facturas", "normal"),
    ("R1", "Errores 80.1, 80.2, 80.6 LIVA", "abono"),
    ("R2", "Artículo 80.3 LIVA", "abono"),
    ("R3", "Artículo 80.4 LIVA", "abono"),
    ("R4", "Otro motivo (abono)", "abono"),
    ("R5", "Simplificada rectificada", "abono"),
]


async def _comparativa_tipos_factura_impl(
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    nif_titular: Optional[str] = None,
):
    """Contadores por `tipo_factura` en el scope filtrado.

    Refactor iter25: elimina el `$lookup` sobre 1M+ docs para
    `_sin_clasificar`. Ahora `tipo_factura` está denormalizado en
    `facturas_comercial` (backfill iter25), así que:
      - Buckets F1..R5: `$group` en SII por `tipo_factura` (rápido con índice).
      - `_sin_clasificar`: `count_documents` en Comercial donde
        `tipo_factura` es null/ausente (rápido con nuevo índice
        `nif_ejerc_per_tipo_com_idx`).
    De ~15-30s en cache-miss pasa a <500ms.
    """
    filtro_sii, filtro_com = await _build_filtros(
        ejercicio, periodo, num_serie=None,
        excluir_base_cero=False,
        nif_titular=nif_titular,
    )
    # Contadores por tipo_factura en SII
    sii_pipe = [
        {"$match": filtro_sii},
        {"$group": {"_id": "$tipo_factura", "n": {"$sum": 1}}},
    ]
    sii_counts: dict[str, int] = {}
    for r in await _db.facturas_sii.aggregate(sii_pipe).to_list(None):
        tipo = r.get("_id") or "(null)"
        sii_counts[tipo] = int(r.get("n") or 0)

    # `_sin_clasificar`: comerciales sin `tipo_factura` (backfill ya lo
    # marca null cuando no hay match SII). count_documents con el índice
    # compuesto es sub-segundo.
    sin_clasificar = await _db.facturas_comercial.count_documents({
        **filtro_com,
        "$or": [
            {"tipo_factura": None},
            {"tipo_factura": ""},
            {"tipo_factura": {"$exists": False}},
        ],
    })

    items = [
        {"code": code, "label": label, "categoria": cat, "n": sii_counts.get(code, 0)}
        for code, label, cat in _TIPOS_FACTURA_CATALOG
    ]
    items.append({
        "code": "_sin_clasificar",
        "label": "Sólo en Comercial (sin tipo)",
        "categoria": "otros",
        "n": sin_clasificar,
    })
    return {"items": items, "total": sum(i["n"] for i in items)}


# ---------------------------------------------------------------------------
# Cuadro de Conciliación Mensual (por sociedad / ejercicio)
# ---------------------------------------------------------------------------
# Devuelve un pivot por (periodo, tipo_factura) con Base + Cuota + Nº de
# facturas para SII y cada origen comercial (SIGLO / SAP FI), las diferencias
# y el % de conciliación por importe y por número de facturas.
#
# Diseño de rendimiento:
#   - 2 aggregations en paralelo (SII / Comercial). Ambos usan $group tras
#     $match sobre índices (nif_titular, ejercicio) → memoria acotada.
#   - El comercial cruza vía $lookup con SII para heredar `tipo_factura`
#     (que no existe en `facturas_comercial`). Los comerciales sin match
#     SII van al bucket `_sin_clasificar`.
#   - matches_num_serie (para % conciliación por número de facturas) se
#     calcula con un tercer aggregation que agrupa comerciales-con-match.
#   - Cacheado con TTL de 5 min (mismo pool que el resto de comparativa).

@router.get("/comparativa/cuadro-mensual")
async def comparativa_cuadro_mensual(
    nif_titular: str,
    ejercicio: str,
    periodo: Optional[str] = None,
):
    """Cuadro de conciliación mensual para una sociedad y un ejercicio.

    Params obligatorios: `nif_titular`, `ejercicio`. Opcional `periodo`
    (CSV de meses "01,02,..."). Devuelve una fila por combinación
    (periodo, tipo_factura) presente en SII o en Comercial.

    Estructura de respuesta:
    {
      "filtros": {"nif_titular": "...", "ejercicio": "2024", "periodo": "..."},
      "origenes": ["SIGLO", "SAP"],  # detectados en scope
      "rows": [
        {
          "periodo": "01",
          "tipo_factura": "F1",
          "sii": {"base": .., "cuota": .., "n": ..},
          "comercial_por_origen": {
             "SIGLO": {"base": .., "cuota": .., "n": ..},
             "SAP":   {"base": .., "cuota": .., "n": .., "invertido": true}
          },
          "delta_por_origen": {
             "SIGLO": {"base": ..(sii-siglo).., "cuota": .., "n": ..},
             "SAP":   {...}
          },
          "pct_conciliacion_por_origen": {
             "SIGLO": {"base": .., "cuota": .., "facturas": ..},
             "SAP":   {...}
          }
        },
        ...
      ],
      "totales": { ... misma estructura pero como fila TOTAL ... }
    }
    """
    if not nif_titular or not ejercicio:
        raise HTTPException(400, "nif_titular y ejercicio son obligatorios")
    cache_key = ("cuadro-mensual", nif_titular.upper(), str(ejercicio), periodo)
    return await _cached_or_compute(
        cache_key,
        lambda: _comparativa_cuadro_mensual_impl(
            nif_titular=nif_titular.upper(),
            ejercicio=str(ejercicio),
            periodo=periodo,
        ),
    )


@router.post("/admin/backfill-tipo-factura")
async def admin_backfill_tipo_factura(
    nif_titular: Optional[str] = None,
    _: dict = Depends(require_permission("sii.wipe")),
):
    """Lanza el backfill de `tipo_factura` desde SII a Comercial.

    Ejecución síncrona (bloquea el request hasta terminar; con 1M docs
    puede tardar 1-2 min). El endpoint devuelve el reporte con contadores.

    Idempotente: se puede lanzar múltiples veces. Sólo `POST` para
    evitar disparos accidentales desde el navegador.
    """
    from backfill_tipo_factura import (
        backfill_tipo_factura_comercial,
        backfill_snapshot_sii_en_comercial,
        backfill_importe_total_comercial,
        ensure_indexes_iter25,
        ensure_indexes_iter26,
    )
    await ensure_indexes_iter25(_db, _logger)
    await ensure_indexes_iter26(_db, _logger)
    report_tipo = await backfill_tipo_factura_comercial(
        _db, _logger, nif_titular=nif_titular,
    )
    report_snapshot = await backfill_snapshot_sii_en_comercial(
        _db, _logger, nif_titular=nif_titular,
    )
    report_importe = await backfill_importe_total_comercial(
        _db, _logger, nif_titular=nif_titular,
    )
    invalidate_comparativa_cache()
    return {
        "ok": True,
        "report": report_tipo,
        "report_snapshot": report_snapshot,
        "report_importe_total": report_importe,
    }


async def _comparativa_cuadro_mensual_impl(
    nif_titular: str,
    ejercicio: str,
    periodo: Optional[str] = None,
):
    config = await _load_comparativa_config()
    filtro_sii, filtro_com = await _build_filtros(
        ejercicio=ejercicio,
        periodo=periodo,
        num_serie=None,
        excluir_base_cero=config["excluir_comercial_base_cero"],
        nif_titular=nif_titular,
        tipos_factura=None,
    )
    excluir_tipo_iva_cero = config.get("excluir_comercial_tipo_iva_cero", True)
    inv_map: dict = config.get("invertir_signo_por_origen") or {}

    # --- SII: $group por (periodo, tipo_factura) sumando detalle o cabecera --
    # iter27: fallback canónico — si base+cuota=0 pero importe_total>0
    # (facturas No Sujeta), tratamos importe_total como base para que
    # el cuadro refleje el peso económico real.
    sii_pipeline = [
        {"$match": filtro_sii},
        {"$project": {
            "_id": 0,
            "periodo": {"$ifNull": ["$periodo", "??"]},
            "tipo_factura": {"$ifNull": ["$tipo_factura", "??"]},
            "_base_raw": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    {"$reduce": {
                        "input": {"$ifNull": ["$detalle_iva", []]},
                        "initialValue": 0.0,
                        "in": {"$add": [
                            "$$value",
                            {"$toDouble": {"$ifNull": ["$$this.base_imponible", 0]}},
                        ]},
                    }},
                    {"$toDouble": {"$ifNull": ["$base_imponible", 0]}},
                ],
            },
            "_cuota_raw": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    {"$reduce": {
                        "input": {"$ifNull": ["$detalle_iva", []]},
                        "initialValue": 0.0,
                        "in": {"$add": [
                            "$$value",
                            {"$toDouble": {"$ifNull": ["$$this.cuota_repercutida", 0]}},
                        ]},
                    }},
                    {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}},
                ],
            },
            "_importe_total": {"$toDouble": {"$ifNull": ["$importe_total", 0]}},
        }},
        {"$addFields": {
            "_base": {
                "$cond": [
                    {"$and": [
                        {"$lte": [{"$abs": {"$add": ["$_base_raw", "$_cuota_raw"]}}, 0.01]},
                        {"$gt": [{"$abs": "$_importe_total"}, 0.01]},
                    ]},
                    "$_importe_total",
                    "$_base_raw",
                ],
            },
            "_cuota": "$_cuota_raw",
        }},
        {"$group": {
            "_id": {"periodo": "$periodo", "tipo": "$tipo_factura"},
            "base": {"$sum": "$_base"},
            "cuota": {"$sum": "$_cuota"},
            "n": {"$sum": 1},
        }},
    ]

    # --- Comercial: $lookup para heredar `tipo_factura` desde SII match ------
    if excluir_tipo_iva_cero:
        det_base_expr = {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$cond": [
                    {"$and": [
                        {"$ne": [{"$ifNull": ["$$this.tipo_impositivo", None]}, None]},
                        {"$ne": [{"$toDouble": {"$ifNull": ["$$this.tipo_impositivo", 0]}}, 0]},
                    ]},
                    {"$toDouble": {"$ifNull": ["$$this.base_imponible", 0]}},
                    0.0,
                ]},
            ]},
        }}
        det_cuota_expr = {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$cond": [
                    {"$and": [
                        {"$ne": [{"$ifNull": ["$$this.tipo_impositivo", None]}, None]},
                        {"$ne": [{"$toDouble": {"$ifNull": ["$$this.tipo_impositivo", 0]}}, 0]},
                    ]},
                    {"$toDouble": {"$ifNull": ["$$this.cuota_repercutida", 0]}},
                    0.0,
                ]},
            ]},
        }}
        header_base_expr = {"$cond": [
            {"$and": [
                {"$ne": [{"$ifNull": ["$tipo_impositivo", None]}, None]},
                {"$ne": [{"$toDouble": {"$ifNull": ["$tipo_impositivo", 0]}}, 0]},
            ]},
            {"$toDouble": {"$ifNull": ["$base_imponible", 0]}},
            0.0,
        ]}
        header_cuota_expr = {"$cond": [
            {"$and": [
                {"$ne": [{"$ifNull": ["$tipo_impositivo", None]}, None]},
                {"$ne": [{"$toDouble": {"$ifNull": ["$tipo_impositivo", 0]}}, 0]},
            ]},
            {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}},
            0.0,
        ]}
    else:
        det_base_expr = {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$toDouble": {"$ifNull": ["$$this.base_imponible", 0]}},
            ]},
        }}
        det_cuota_expr = {"$reduce": {
            "input": {"$ifNull": ["$detalle_iva", []]},
            "initialValue": 0.0,
            "in": {"$add": [
                "$$value",
                {"$toDouble": {"$ifNull": ["$$this.cuota_repercutida", 0]}},
            ]},
        }}
        header_base_expr = {"$toDouble": {"$ifNull": ["$base_imponible", 0]}}
        header_cuota_expr = {"$toDouble": {"$ifNull": ["$cuota_repercutida", 0]}}

    # Refactor iter25: `tipo_factura` denormalizado en Comercial → no
    # necesitamos `$lookup` con SII. n_matched (facturas comerciales que
    # tienen contraparte SII) = las que tienen tipo_factura no-null.
    # iter27: fallback canónico también aquí (mismo que en SII).
    com_pipeline = [
        {"$match": filtro_com},
        {"$project": {
            "_id": 0,
            "periodo": {"$ifNull": ["$periodo", "??"]},
            "origen": {"$ifNull": ["$origen_comercial", "DESCONOCIDO"]},
            "_tipo": {"$ifNull": ["$tipo_factura", "_sin_clasificar"]},
            "_base_raw": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    det_base_expr,
                    header_base_expr,
                ],
            },
            "_cuota_raw": {
                "$cond": [
                    {"$gt": [{"$size": {"$ifNull": ["$detalle_iva", []]}}, 0]},
                    det_cuota_expr,
                    header_cuota_expr,
                ],
            },
            "_importe_total": {"$toDouble": {"$ifNull": ["$importe_total", 0]}},
            "_matched": {"$cond": [
                {"$and": [
                    {"$ne": [{"$ifNull": ["$tipo_factura", None]}, None]},
                    {"$ne": ["$tipo_factura", ""]},
                ]},
                True, False,
            ]},
        }},
        {"$addFields": {
            "_base": {
                "$cond": [
                    {"$and": [
                        {"$lte": [{"$abs": {"$add": ["$_base_raw", "$_cuota_raw"]}}, 0.01]},
                        {"$gt": [{"$abs": "$_importe_total"}, 0.01]},
                    ]},
                    "$_importe_total",
                    "$_base_raw",
                ],
            },
            "_cuota": "$_cuota_raw",
        }},
        {"$group": {
            "_id": {
                "periodo": "$periodo",
                "tipo": "$_tipo",
                "origen": "$origen",
            },
            "base": {"$sum": "$_base"},
            "cuota": {"$sum": "$_cuota"},
            "n": {"$sum": 1},
            "n_matched": {"$sum": {"$cond": ["$_matched", 1, 0]}},
        }},
    ]

    import asyncio as _aio
    sii_res, com_res = await _aio.gather(
        _db.facturas_sii.aggregate(sii_pipeline, allowDiskUse=True).to_list(None),
        _db.facturas_comercial.aggregate(com_pipeline, allowDiskUse=True).to_list(None),
    )

    # Estructura intermedia: rows[(periodo,tipo)] = {sii, com_por_origen, matches_por_origen}
    rows: dict[tuple, dict] = {}
    origenes_set: set[str] = set()

    def _row(k):
        return rows.setdefault(k, {
            "periodo": k[0],
            "tipo_factura": k[1],
            "sii": {"base": 0.0, "cuota": 0.0, "n": 0},
            "comercial_por_origen": {},
            "matches_por_origen": {},
        })

    for r in sii_res:
        per = r["_id"].get("periodo") or "??"
        tipo = r["_id"].get("tipo") or "??"
        row = _row((per, tipo))
        row["sii"] = {
            "base": round(float(r.get("base") or 0), 2),
            "cuota": round(float(r.get("cuota") or 0), 2),
            "n": int(r.get("n") or 0),
        }

    for r in com_res:
        per = r["_id"].get("periodo") or "??"
        tipo = r["_id"].get("tipo") or "_sin_clasificar"
        origen = r["_id"].get("origen") or "DESCONOCIDO"
        origenes_set.add(origen)
        base = float(r.get("base") or 0)
        cuota = float(r.get("cuota") or 0)
        invertido = bool(inv_map.get(origen))
        if invertido:
            base = -base
            cuota = -cuota
        row = _row((per, tipo))
        row["comercial_por_origen"][origen] = {
            "base": round(base, 2),
            "cuota": round(cuota, 2),
            "n": int(r.get("n") or 0),
            "invertido": invertido,
        }
        row["matches_por_origen"][origen] = int(r.get("n_matched") or 0)

    # Helper de % conciliación (mismo estilo que /comparativa/totales).
    def _pct(sii_val: float, com_val: float) -> Optional[float]:
        if sii_val == 0 and com_val == 0:
            return 1.0
        denom = abs(sii_val) if sii_val != 0 else abs(com_val)
        if denom == 0:
            return None
        return round(1.0 - abs(sii_val - com_val) / denom, 6)

    def _pct_facturas(n_sii: int, n_com: int, matches: int) -> Optional[float]:
        universo = n_sii + n_com - matches
        if universo <= 0:
            return 1.0 if (n_sii == 0 and n_com == 0) else None
        return round(matches / universo, 6)

    origenes = sorted(origenes_set)

    # Materializar filas con comercial_total agregado + delta/pct únicos
    # (SII vs Σ comerciales). Los detalles por origen se mantienen para
    # display, pero el Δ/%% conciliación se calculan sobre el total.
    result_rows: list[dict] = []
    for (per, tipo), row in rows.items():
        sii = row["sii"]
        # Rellena orígenes ausentes con 0 y suma comercial_total
        c_tot = {"base": 0.0, "cuota": 0.0, "n": 0}
        matches_total = 0
        for og in origenes:
            com = row["comercial_por_origen"].get(og) or {
                "base": 0.0, "cuota": 0.0, "n": 0, "invertido": bool(inv_map.get(og)),
            }
            row["comercial_por_origen"][og] = com
            c_tot["base"] += com["base"]
            c_tot["cuota"] += com["cuota"]
            c_tot["n"] += com["n"]
            matches_total += row["matches_por_origen"].get(og, 0)
        c_tot["base"] = round(c_tot["base"], 2)
        c_tot["cuota"] = round(c_tot["cuota"], 2)
        row["comercial_total"] = c_tot
        row["delta"] = {
            "base": round(sii["base"] - c_tot["base"], 2),
            "cuota": round(sii["cuota"] - c_tot["cuota"], 2),
            "n": sii["n"] - c_tot["n"],
        }
        row["pct_conciliacion"] = {
            "base": _pct(sii["base"], c_tot["base"]),
            "cuota": _pct(sii["cuota"], c_tot["cuota"]),
            "facturas": _pct_facturas(sii["n"], c_tot["n"], matches_total),
        }
        row.pop("matches_por_origen", None)
        result_rows.append(row)

    # Orden: por periodo ASC, luego tipo_factura (F1,F2,...,R1..R5,_sin_clasificar)
    TIPO_ORDER = {
        "F1": 1, "F2": 2, "F3": 3, "F4": 4,
        "R1": 5, "R2": 6, "R3": 7, "R4": 8, "R5": 9,
        "_sin_clasificar": 99, "??": 100,
    }
    result_rows.sort(key=lambda r: (
        r["periodo"],
        TIPO_ORDER.get(r["tipo_factura"], 50),
        r["tipo_factura"],
    ))

    # Fila de totales globales (suma de todas las filas)
    total_sii = {"base": 0.0, "cuota": 0.0, "n": 0}
    total_com: dict[str, dict] = {
        og: {"base": 0.0, "cuota": 0.0, "n": 0, "invertido": bool(inv_map.get(og))}
        for og in origenes
    }
    total_matches_all: int = 0
    for r in result_rows:
        total_sii["base"] += r["sii"]["base"]
        total_sii["cuota"] += r["sii"]["cuota"]
        total_sii["n"] += r["sii"]["n"]
        for og in origenes:
            c = r["comercial_por_origen"].get(og) or {}
            total_com[og]["base"] += float(c.get("base") or 0)
            total_com[og]["cuota"] += float(c.get("cuota") or 0)
            total_com[og]["n"] += int(c.get("n") or 0)
    # matches totales acumulados (todos los orígenes; num_serie es único
    # en `facturas_comercial` así que no hay doble-conteo).
    for r in com_res:
        total_matches_all += int(r.get("n_matched") or 0)

    total_comercial: dict = {"base": 0.0, "cuota": 0.0, "n": 0}
    for og in origenes:
        c = total_com[og]
        c["base"] = round(c["base"], 2)
        c["cuota"] = round(c["cuota"], 2)
        total_comercial["base"] += c["base"]
        total_comercial["cuota"] += c["cuota"]
        total_comercial["n"] += c["n"]
    total_comercial["base"] = round(total_comercial["base"], 2)
    total_comercial["cuota"] = round(total_comercial["cuota"], 2)

    total_sii["base"] = round(total_sii["base"], 2)
    total_sii["cuota"] = round(total_sii["cuota"], 2)

    total_delta = {
        "base": round(total_sii["base"] - total_comercial["base"], 2),
        "cuota": round(total_sii["cuota"] - total_comercial["cuota"], 2),
        "n": total_sii["n"] - total_comercial["n"],
    }
    total_pct = {
        "base": _pct(total_sii["base"], total_comercial["base"]),
        "cuota": _pct(total_sii["cuota"], total_comercial["cuota"]),
        "facturas": _pct_facturas(
            total_sii["n"], total_comercial["n"], total_matches_all,
        ),
    }

    return {
        "filtros": {
            "nif_titular": nif_titular,
            "ejercicio": ejercicio,
            "periodo": periodo,
        },
        "origenes": origenes,
        "rows": result_rows,
        "totales": {
            "sii": total_sii,
            "comercial_por_origen": total_com,
            "comercial_total": total_comercial,
            "delta": total_delta,
            "pct_conciliacion": total_pct,
        },
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
        """Streaming generator: emite fila a fila sin cargar universos a RAM.

        Estrategia (misma que los fast-paths del listado):
          - Ruta 1 (comercial): cursor sobre `facturas_comercial` + $lookup
            fila a fila contra SII → clasifica coincide/discrepancia/
            solo_comercial y emite. Memoria constante.
          - Ruta 2 (solo_sii): cursor sobre `facturas_sii` + $lookup inverso
            a comercial → emite las que no tienen contraparte.

        Antes: `com_docs.to_list(length=None)` cargaba 1,5M docs → OOM →
        el CSV terminaba con solo la cabecera (bug reportado).
        """
        # BOM para Excel + cabecera
        yield ("\ufeff" + _writerow_to_str(headers)).encode("utf-8")

        nif_norm = (
            str(nif_titular).strip().upper() if nif_titular else None
        )
        _plist = (
            [p.strip() for p in str(periodo).split(",") if p.strip()]
            if periodo else []
        )

        # ---------------- Ruta 1: iteración por Comercial ----------------
        # Cuando `estado=solo_sii`, no hace falta iterar comerciales.
        if estado != "solo_sii":
            # Aggregation streaming: $lookup por num_serie único de SII.
            # Escala con cualquier volumen; no consume RAM del proceso.
            sii_extra_conds: list[dict] = []
            if nif_norm:
                sii_extra_conds.append({"$eq": ["$_sii.nif_titular", nif_norm]})
            if ejercicio:
                sii_extra_conds.append({"$eq": ["$_sii.ejercicio", str(ejercicio)]})
            if len(_plist) == 1:
                sii_extra_conds.append({"$eq": ["$_sii.periodo", _plist[0]]})
            elif len(_plist) > 1:
                sii_extra_conds.append({"$in": ["$_sii.periodo", _plist]})

            com_pipeline: list[dict] = [
                {"$match": filtro_com},
                {"$lookup": {
                    "from": "facturas_sii",
                    "localField": "num_serie_factura",
                    "foreignField": "num_serie_factura",
                    "as": "_sii_docs",
                }},
                {"$addFields": {
                    "_sii": {"$arrayElemAt": ["$_sii_docs", 0]},
                }},
            ]
            if sii_extra_conds:
                com_pipeline.append({"$addFields": {
                    "_has_sii": {"$and": [
                        {"$gt": [{"$size": "$_sii_docs"}, 0]},
                        *sii_extra_conds,
                    ]},
                }})
            else:
                com_pipeline.append({"$addFields": {
                    "_has_sii": {"$gt": [{"$size": "$_sii_docs"}, 0]},
                }})
            com_pipeline.append({"$project": {"_sii_docs": 0, "versiones": 0}})

            async for doc in _db.facturas_comercial.aggregate(
                com_pipeline, allowDiskUse=True,
            ):
                sii = doc.pop("_sii", None) or None
                has_sii = bool(doc.pop("_has_sii", False))
                doc.pop("_id", None)
                if sii and not has_sii:
                    # El SII lookupeado no cumple el ámbito → descartamos
                    # como si no hubiera match (será solo_comercial).
                    sii = None
                # Sanea el SII de campos internos que _build_row_from_docs
                # no necesita.
                if isinstance(sii, dict):
                    sii.pop("_id", None)
                    sii.pop("versiones", None)
                r = _build_row_from_docs(
                    sii, doc, doc.get("num_serie_factura"), config,
                )
                # Filtros de estado
                if estado and r["estado"] != estado:
                    continue
                if estado is None and only_diffs and r["estado"] == "coincide":
                    continue
                yield _writerow_to_str(_row_to_cells(r)).encode("utf-8")

        # ---------------- Ruta 2: solo_sii ------------------------------
        # Cuando `estado=solo_sii` o queremos incluir esas filas en el
        # export completo (estado=None con only_diffs true/false, ambos las
        # incluyen porque nunca son "coincide").
        incluir_solo_sii = estado in (None, "solo_sii")
        if incluir_solo_sii:
            com_extra_conds: list[dict] = []
            if nif_norm:
                com_extra_conds.append({"$eq": ["$$c.nif_titular", nif_norm]})
            if ejercicio:
                com_extra_conds.append({"$eq": ["$$c.ejercicio", str(ejercicio)]})
            if len(_plist) == 1:
                com_extra_conds.append({"$eq": ["$$c.periodo", _plist[0]]})
            elif len(_plist) > 1:
                com_extra_conds.append({"$in": ["$$c.periodo", _plist]})

            if com_extra_conds:
                has_valid_com = {"$and": [
                    {"$gt": [{"$size": "$_com_docs"}, 0]},
                    {"$anyElementTrue": {"$map": {
                        "input": "$_com_docs",
                        "as": "c",
                        "in": {"$and": com_extra_conds},
                    }}},
                ]}
            else:
                has_valid_com = {"$gt": [{"$size": "$_com_docs"}, 0]}

            sii_pipeline: list[dict] = [
                {"$match": filtro_sii},
                {"$lookup": {
                    "from": "facturas_comercial",
                    "localField": "num_serie_factura",
                    "foreignField": "num_serie_factura",
                    "as": "_com_docs",
                }},
                {"$addFields": {"_has_valid_com": has_valid_com}},
                {"$match": {"_has_valid_com": False}},
                {"$project": {"_id": 0, "_com_docs": 0, "_has_valid_com": 0, "versiones": 0}},
            ]
            async for d in _db.facturas_sii.aggregate(
                sii_pipeline, allowDiskUse=True,
            ):
                r = _build_row_from_docs(
                    d, None, d.get("num_serie_factura"), config,
                )
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
    import_id: Optional[str] = None,
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
        if import_id:
            await finish_import(
                _db, import_id,
                status="done" if final_status == "completed" else "error",
                total_procesados=len(facturas),
                insertados=len(facturas),
                actualizados=0,
                error_message=None if final_status == "completed" else "Cancelado por el usuario",
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
        if import_id:
            await finish_import(
                _db, import_id, status="error",
                error_message=f"{type(exc).__name__}: {exc}",
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
    user: dict = Depends(get_current_user),
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

    import_id = await start_import(
        _db,
        origen="sii",
        fuente="consulta_mensual_aeat",
        file_name=None,
        file_size_bytes=None,
        user_id=user.get("_id") or user.get("id"),
        user_email=user.get("email"),
        nif_titular=nif_titular,
        ejercicio=ejercicio,
        periodo=periodo,
        job_id=job_id,
        extra={"entorno": entorno, "max_paginas": max_paginas},
    )

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
            "import_id": import_id,
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
            import_id=import_id,
        )
    )
    return {"job_id": job_id, "status": "queued", "import_id": import_id}


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
