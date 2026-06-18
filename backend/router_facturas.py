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

from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone
import asyncio
import csv
import io
import random
import hashlib
import time
import uuid

from factura_model import (
    CAMPOS_CANONICOS,
    CAMPOS_NUMERICOS,
    FacturaDatos,
    FacturaVersion,
    diff_facturas,
    normalize_factura_row,
)
from sii_client import ENDPOINTS, WSDL_URL, build_client, get_default_mode


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


def _mock_factura_mensual(
    nif_titular: str,
    nombre_titular: str,
    ejercicio: str,
    periodo: str,
    idx: int,
) -> dict:
    """Genera datos deterministas de una factura para mock mensual."""
    seed = f"{nif_titular}|{ejercicio}|{periodo}|{idx}"
    rnd = random.Random(int(hashlib.md5(seed.encode()).hexdigest()[:8], 16))
    base = round(50 + rnd.random() * 950, 2)
    tipo = rnd.choice([4.0, 10.0, 21.0])
    cuota = round(base * tipo / 100, 2)
    total = round(base + cuota, 2)
    dia = rnd.randint(1, 28)
    return {
        "num_serie_factura": f"F{ejercicio}-{periodo}-{idx:04d}",
        "fecha_expedicion": f"{dia:02d}-{periodo}-{ejercicio}",
        "nif_emisor": nif_titular,
        "nombre_emisor": nombre_titular,
        "ejercicio": ejercicio,
        "periodo": periodo,
        "nif_titular": nif_titular,
        "contraparte_nif": f"B{rnd.randint(10**7, 10**8 - 1)}",
        "contraparte_nombre": f"Cliente {idx}",
        "tipo_factura": "F1",
        "clave_regimen_especial": "01",
        "descripcion_operacion": f"Servicios prestados {periodo}/{ejercicio}",
        "fecha_operacion": f"{dia:02d}-{periodo}-{ejercicio}",
        "base_imponible": base,
        "tipo_impositivo": tipo,
        "cuota_repercutida": cuota,
        "importe_total": total,
    }


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
    mode: Optional[str] = Form(None),
    cert_password: Optional[str] = Form(None),
    certificate: Optional[UploadFile] = File(None),
    max_paginas: Optional[int] = Form(None),
):
    """Consulta mensual al SII. Si se aporta certificado se invoca el SOAP
    real; si no, se usa mock determinista.

    El certificado NO se guarda en el servidor.
    """
    cert_bytes = None
    if certificate is not None:
        cert_bytes = await certificate.read()
        if not cert_bytes:
            cert_bytes = None
    effective_mode = "real" if cert_bytes else (mode or get_default_mode())

    facturas: list[dict] = []
    start_ts = datetime.now(timezone.utc)
    log_entry = {
        "id": __import__("uuid").uuid4().hex,
        "timestamp": start_ts.isoformat(),
        "operation": "ConsultaLRFacturasEmitidas.Mensual",
        "endpoint": ENDPOINTS.get(entorno, ""),
        "entorno": entorno,
        "sii_mode": effective_mode,
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
        if effective_mode == "real":
            try:
                client = build_client(
                    "real", cert_bytes=cert_bytes, cert_password=cert_password
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
        else:
            seed = f"{nif_titular}|{ejercicio}|{periodo}"
            n_facts = (
                int(hashlib.md5(seed.encode()).hexdigest()[:2], 16) % 8 + 3
            )
            facturas = [
                _mock_factura_mensual(
                    nif_titular, nombre_titular, ejercicio, periodo, i
                )
                for i in range(1, n_facts + 1)
            ]
            log_entry["request_xml"] = (
                f"<!-- mock consulta mensual {nif_titular} {ejercicio}/{periodo} -->"
            )
            log_entry["response_xml"] = (
                f"<!-- mock: {n_facts} facturas generadas deterministicamente -->"
            )

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
        "sii_mode": effective_mode,
        "facturas": facturas,
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

    return base_tot, cuota_tot, tipo, lineas


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
    return base, cuota, tipo, detalle_iva


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
        registros, errores = _parsear_report_tabular(text, origen_detectado)
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
    },
}


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

    registros_por_num: dict[str, dict] = {}
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


def _build_row_from_docs(sii: Optional[dict], com: Optional[dict], ns: str) -> dict:
    if sii and com:
        d = diff_facturas(sii, com)
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
) -> tuple[dict, dict]:
    """Construye filtros Mongo para SII y comercial, restringiendo SII al
    universo (ejercicio,periodo) de comercial si no se filtra explícitamente.
    """
    import re

    filtro_sii: dict = {}
    filtro_com: dict = {}
    if ejercicio:
        filtro_sii["ejercicio"] = str(ejercicio)
        filtro_com["ejercicio"] = str(ejercicio)
    if periodo:
        filtro_sii["periodo"] = str(periodo)
        filtro_com["periodo"] = str(periodo)
    if num_serie:
        regex_ns = {"$regex": re.escape(num_serie), "$options": "i"}
        filtro_sii["num_serie_factura"] = regex_ns
        filtro_com["num_serie_factura"] = regex_ns

    if not ejercicio and not periodo:
        pares_com = await _db.facturas_comercial.aggregate([
            {"$group": {
                "_id": {"ejercicio": "$ejercicio", "periodo": "$periodo"},
            }},
        ]).to_list(length=None)
        pares = [p["_id"] for p in pares_com if p["_id"].get("ejercicio")]
        if pares:
            filtro_sii["$or"] = [
                {"ejercicio": p["ejercicio"], "periodo": p["periodo"]}
                for p in pares
            ]

    return filtro_sii, filtro_com


async def _comparativa_data(
    ejercicio: Optional[str],
    periodo: Optional[str],
    only_diffs: bool,
    num_serie: Optional[str] = None,
    estado: Optional[str] = None,
) -> list[dict]:
    """Versión legacy (carga todo en memoria). Mantenida para `/export` y
    en escenarios sin necesidad de paginación a nivel BD.
    Para listado paginado usar `comparativa()` directamente, que ya optimiza
    los estados que no requieren cargar todo el SII."""
    filtro_sii, filtro_com = await _build_filtros(ejercicio, periodo, num_serie)

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
        _build_row_from_docs(sii_map.get(ns), com_map.get(ns), ns)
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
):
    """Compara facturas SII vs Comercial por `num_serie_factura`.

    Filtros: `ejercicio`, `periodo`, `num_serie` (contiene), `estado`
    (coincide | discrepancia | solo_sii | solo_comercial).
    Paginación: `skip` / `limit` (default 50).

    Optimización: para evitar cargar millones de facturas SII en memoria,
    construimos los resultados desde el universo comercial (que siempre es
    pequeño) y sólo cargamos SII docs cuyo `num_serie` aparece en comercial.
    El estado `solo_sii` requiere escanear SII fuera del comercial y se
    pagina a nivel BD para no consumir memoria.
    """
    filtro_sii, filtro_com = await _build_filtros(ejercicio, periodo, num_serie)

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
        filas_com.append(_build_row_from_docs(sii, com, ns))

    # 4) Contar SII fuera del comercial → estado solo_sii
    solo_sii_filter = {**filtro_sii, "num_serie_factura": {"$nin": com_keys}}
    solo_sii_total = await _db.facturas_sii.count_documents(solo_sii_filter)

    # 5) Aplicar filtros only_diffs / estado para decidir total e items
    if estado == "solo_sii":
        # Sólo SII: paginamos a nivel BD. No mezclamos con filas_com.
        cursor = _db.facturas_sii.find(
            solo_sii_filter, {"_id": 0, "versiones": 0}
        ).sort("num_serie_factura", 1).skip(skip).limit(limit)
        sii_pagina = await cursor.to_list(length=limit)
        items = [
            _build_row_from_docs(d, None, d["num_serie_factura"])
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

        filas.sort(key=lambda r: r["num_serie_factura"])
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


@router.get("/comparativa/periodos")
async def comparativa_periodos():
    """Devuelve los ejercicios/periodos distintos disponibles en `facturas_sii`
    (combinados con los de `facturas_comercial`) para poblar los filtros.

    Usa aggregation con `$group` apoyado en el índice compuesto
    (ejercicio, periodo) → segundos en lugar de minutos sobre 1M+ docs.
    """
    async def _distinct_eje_per(col):
        cursor = col.aggregate([
            {"$group": {
                "_id": {"ejercicio": "$ejercicio", "periodo": "$periodo"},
            }},
        ])
        docs = await cursor.to_list(length=None)
        eje = {d["_id"].get("ejercicio") for d in docs if d["_id"].get("ejercicio")}
        per = {d["_id"].get("periodo") for d in docs if d["_id"].get("periodo")}
        return eje, per

    sii_eje, sii_per = await _distinct_eje_per(_db.facturas_sii)
    com_eje, com_per = await _distinct_eje_per(_db.facturas_comercial)
    ejercicios = sorted({str(e) for e in (sii_eje | com_eje)})
    periodos = sorted({str(p) for p in (sii_per | com_per)})
    return {"ejercicios": ejercicios, "periodos": periodos}


@router.get("/comparativa/export")
async def comparativa_export(
    only_diffs: bool = True,
    ejercicio: Optional[str] = None,
    periodo: Optional[str] = None,
    num_serie: Optional[str] = None,
    estado: Optional[str] = None,
):
    """Exporta la comparativa completa (sin paginar) a CSV (UTF-8 BOM) abrible
    directamente en Excel/LibreOffice."""
    resultados = await _comparativa_data(
        ejercicio, periodo, only_diffs, num_serie, estado
    )

    headers = ["num_serie_factura", "estado", "campos_con_diferencias"]
    for c in CAMPOS_CANONICOS:
        if c == "num_serie_factura":
            continue
        headers.append(f"sii_{c}")
        headers.append(f"com_{c}")

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM para Excel
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for r in resultados:
        row = [
            r["num_serie_factura"],
            r["estado"],
            ",".join(r["diferencias"].keys()),
        ]
        sii = r.get("sii") or {}
        com = r.get("comercial") or {}
        for c in CAMPOS_CANONICOS:
            if c == "num_serie_factura":
                continue
            row.append(sii.get(c, "") if sii.get(c) is not None else "")
            row.append(com.get(c, "") if com.get(c) is not None else "")
        writer.writerow(row)

    filename = "comparativa"
    if ejercicio:
        filename += f"_{ejercicio}"
    if periodo:
        filename += f"_{periodo}"
    filename += ".csv"

    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
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
    effective_mode: str,
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
        "sii_mode": effective_mode,
        "status": "ok", "http_status": None, "error_message": None,
        "duration_ms": 0, "request_xml": "", "response_xml": "",
        "nif_titular": nif_titular, "nif_emisor": nif_titular,
        "num_serie_factura": None, "consulta_id": None,
        "batch_id": f"job:{job_id}",
    }

    try:
        if effective_mode == "real":
            try:
                client = build_client(
                    "real", cert_bytes=cert_bytes, cert_password=cert_password
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
        else:
            # Mock: simulamos progreso simple
            seed = f"{nif_titular}|{ejercicio}|{periodo}"
            n_facts = int(hashlib.md5(seed.encode()).hexdigest()[:2], 16) % 8 + 3
            facturas = []
            for i in range(1, n_facts + 1):
                facturas.append(
                    _mock_factura_mensual(
                        nif_titular, nombre_titular, ejercicio, periodo, i
                    )
                )
                # Reporta progreso por cada factura simulada (con pausa)
                await _db.jobs.update_one(
                    {"id": job_id},
                    {"$set": {
                        "progress.page": 1,
                        "progress.invoices": i,
                        "updated_at": _now_iso(),
                    }},
                )
                await asyncio.sleep(0.05)
            log_entry["request_xml"] = (
                f"<!-- mock job consulta mensual {nif_titular} "
                f"{ejercicio}/{periodo} -->"
            )
            log_entry["response_xml"] = (
                f"<!-- mock: {n_facts} facturas generadas -->"
            )

        # Persiste facturas en BD: en modo real ya se guardaron página a
        # página via `progress_cb`. En mock no había callback con facturas
        # de página, así que persistimos aquí.
        if effective_mode != "real":
            for f in facturas:
                await upsert_factura("facturas_sii", f, "consulta_mensual")

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
    mode: Optional[str] = Form(None),
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
    effective_mode = "real" if cert_bytes else (mode or get_default_mode())

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
            "sii_mode": effective_mode,
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
            ejercicio, periodo, entorno, effective_mode, max_paginas,
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
    if p.get("sii_mode") == "real" and not cert_bytes:
        raise HTTPException(
            400,
            "El job original era en modo real: vuelve a aportar el "
            "certificado (.pfx) para reanudar.",
        )
    effective_mode = "real" if cert_bytes else p.get("sii_mode", "mock")

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
        "params": {**p, "sii_mode": effective_mode},
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
            effective_mode, p.get("max_paginas"), start_clave=clave,
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
