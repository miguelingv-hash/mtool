"""
SII Consulta API
================
Servicio FastAPI que consulta el estado de facturas emitidas en el SII de la
Agencia Tributaria Española (servicio SOAP ConsultaLRFactEmitidas, WSDL v1.1).

Soporta dos modos:
  - ``mock``  → respuestas simuladas deterministas (desarrollo).
  - ``real``  → invocación SOAP real con autenticación mTLS por certificado
                PKCS#12 (.pfx/.p12), implementada con `zeep`.

El modo activo se decide en este orden de prioridad:
  1. Certificado aportado en la petición (campo ``certificate``) ⇒ ``real``.
  2. Cabecera/parámetro ``mode`` explícito en la petición.
  3. Variable de entorno ``SII_MODE`` (por defecto ``mock``).

WSDL:
https://sede.agenciatributaria.gob.es/static_files/Sede/Procedimiento_ayuda/G417/FicherosSuministros/V_1_1/WSDL/SuministroFactEmitidas.wsdl
"""

from fastapi import (
    FastAPI,
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    Form,
)
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from typing import List, Optional, Literal
from datetime import datetime, timezone
from pathlib import Path
import os
import uuid
import csv
import io
import logging


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env", override=True)

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="Monitor SII API", version="1.1.0")
api_router = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("sii")


# ---------------------------------------------------------------------------
# Modelos (referenciados por sii_client.py - imports diferidos allí)
# ---------------------------------------------------------------------------


class ConsultaInput(BaseModel):
    """Datos de entrada para invocar ConsultaLRFactEmitidas (una factura)."""

    model_config = ConfigDict(extra="ignore")

    nif_titular: str = Field(..., min_length=8, max_length=15)
    nombre_titular: str = Field(..., min_length=1, max_length=120)
    ejercicio: str = Field(..., pattern=r"^\d{4}$")
    periodo: str = Field(..., pattern=r"^(0[1-9]|1[0-2]|1T|2T|3T|4T)$")
    nif_emisor: str = Field(..., min_length=8, max_length=15)
    nombre_emisor: Optional[str] = Field(default=None, max_length=120)
    num_serie_factura: str = Field(..., min_length=1, max_length=60)
    fecha_expedicion: str = Field(..., pattern=r"^\d{2}-\d{2}-\d{4}$")
    entorno: Literal[
        "preproduccion",
        "preproduccion_sello",
        "produccion",
        "produccion_sello",
    ] = "preproduccion"


class RespuestaSII(BaseModel):
    estado_envio: str
    estado_factura: str
    codigo_error_registro: Optional[str] = None
    descripcion_error_registro: Optional[str] = None
    timestamp_presentacion: Optional[str] = None
    num_registro_presentacion: Optional[str] = None
    csv: Optional[str] = None
    endpoint: str
    wsdl: str


class ConsultaRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    modo: Literal["unitaria", "batch"] = "unitaria"
    sii_mode: Literal["mock", "real"] = "mock"
    batch_id: Optional[str] = None
    entrada: ConsultaInput
    respuesta: RespuestaSII
    soap_request_xml: str
    soap_response_xml: str


class ErrorFila(BaseModel):
    fila: int
    motivo: str
    datos: dict


class BatchResumen(BaseModel):
    batch_id: str
    sii_mode: str
    total: int
    correctas: int
    aceptadas_con_errores: int
    anuladas: int
    no_registradas: int
    errores_validacion: int
    errores: List[ErrorFila] = []
    registros: List[ConsultaRecord]


class StatsResponse(BaseModel):
    total: int
    correctas: int
    aceptadas_con_errores: int
    anuladas: int
    no_registradas: int
    ultimas: List[ConsultaRecord]


class SIIConfigResponse(BaseModel):
    default_mode: Literal["mock", "real"]
    server_cert_configured: bool
    real_mode_available: bool
    wsdl: str
    endpoints: dict


class WsLog(BaseModel):
    """Log de una invocación al WS del SII (ok o error)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    operation: str = "ConsultaLRFacturasEmitidas"
    endpoint: str = ""
    entorno: str = ""
    sii_mode: str = "mock"
    status: Literal["ok", "error"] = "ok"
    http_status: Optional[int] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    request_xml: str = ""
    response_xml: str = ""
    consulta_id: Optional[str] = None
    batch_id: Optional[str] = None
    # Contexto rápido para listado/filtrado
    nif_titular: Optional[str] = None
    nif_emisor: Optional[str] = None
    num_serie_factura: Optional[str] = None


class WsLogListResponse(BaseModel):
    total: int
    items: List[WsLog]


# Importar tras definir modelos (sii_client hace `from server import ...`)
from sii_client import (  # noqa: E402
    ENDPOINTS,
    WSDL_URL,
    build_client,
    get_default_mode,
    server_cert_configured,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_cert(certificate: Optional[UploadFile]) -> Optional[bytes]:
    if certificate is None:
        return None
    data = await certificate.read()
    if not data:
        return None
    return data


def _resolve_mode(mode: Optional[str], has_cert: bool) -> str:
    if has_cert:
        return "real"
    return (mode or get_default_mode()).lower()


async def _log_ws_call(log: WsLog) -> WsLog:
    """Persiste un log de invocación al WS del SII."""
    try:
        data = log.model_dump()
        # Mongo BSON limit 16MB: truncamos XML grandes (ver router_facturas.MAX_XML_LOG).
        from router_facturas import _truncar_xml
        data["request_xml"] = _truncar_xml(data.get("request_xml", "") or "")
        data["response_xml"] = _truncar_xml(data.get("response_xml", "") or "")
        await db.wslogs.insert_one(data)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo persistir el log del WS")
    return log


async def _execute_and_log(
    client_impl,
    entrada: ConsultaInput,
    effective_mode: str,
    batch_id: Optional[str] = None,
) -> tuple["RespuestaSII", str, str, WsLog, dict | None]:
    """Ejecuta una consulta SII y guarda un WsLog (success o error).

    Devuelve (respuesta, request_xml, response_xml, log, datos_factura).
    `datos_factura` es el dict con los campos canónicos parseados (importes,
    IVA, contraparte) listo para `upsert_factura`, o `None` si la AEAT no
    devolvió registro útil.
    """
    start = datetime.now(timezone.utc)
    log = WsLog(
        operation="ConsultaLRFacturasEmitidas",
        endpoint=ENDPOINTS.get(entrada.entorno, ""),
        entorno=entrada.entorno,
        sii_mode=effective_mode,
        nif_titular=entrada.nif_titular,
        nif_emisor=entrada.nif_emisor,
        num_serie_factura=entrada.num_serie_factura,
        batch_id=batch_id,
    )
    try:
        respuesta, req_xml, resp_xml, datos_factura = client_impl.consultar(
            entrada
        )
    except Exception as exc:  # noqa: BLE001
        log.status = "error"
        log.error_message = str(exc)[:2000]
        log.duration_ms = int(
            (datetime.now(timezone.utc) - start).total_seconds() * 1000
        )
        await _log_ws_call(log)
        raise

    log.request_xml = req_xml
    log.response_xml = resp_xml
    log.http_status = 200
    log.duration_ms = int(
        (datetime.now(timezone.utc) - start).total_seconds() * 1000
    )
    await _log_ws_call(log)
    return respuesta, req_xml, resp_xml, log, datos_factura


async def _invoke_sii(
    entrada: ConsultaInput,
    cert_bytes: Optional[bytes],
    cert_password: Optional[str],
    mode_override: Optional[str],
):
    effective_mode = _resolve_mode(mode_override, bool(cert_bytes))
    try:
        client_impl = build_client(
            effective_mode, cert_bytes=cert_bytes, cert_password=cert_password
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    try:
        respuesta, req_xml, resp_xml, _, datos_factura = await _execute_and_log(
            client_impl, entrada, effective_mode
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error invocando SII (%s)", effective_mode)
        raise HTTPException(502, f"Error en servicio SII: {exc}")

    return respuesta, req_xml, resp_xml, effective_mode, datos_factura


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@api_router.get("/")
async def root():
    return {
        "service": "Monitor SII API",
        "wsdl": WSDL_URL,
        "modo": get_default_mode(),
        "endpoints": ENDPOINTS,
    }


@api_router.get("/sii/config", response_model=SIIConfigResponse)
async def sii_config():
    default = get_default_mode()
    cert_ok = server_cert_configured()
    return SIIConfigResponse(
        default_mode=default if default in ("mock", "real") else "mock",
        server_cert_configured=cert_ok,
        # En real-mode siempre es posible: la UI puede subir el certificado
        real_mode_available=True,
        wsdl=WSDL_URL,
        endpoints=ENDPOINTS,
    )


# --------- Consulta unitaria (JSON ó multipart con certificado) ------------


@api_router.post("/sii/consulta-unitaria", response_model=ConsultaRecord)
async def consulta_unitaria(entrada: ConsultaInput):
    """Consulta unitaria por JSON. Usa el modo configurado en el servidor
    (`SII_MODE`). Para invocación real con certificado aportado en cliente,
    usar ``POST /api/sii/consulta-unitaria-cert``."""
    respuesta, req_xml, resp_xml, effective_mode, datos_factura = await _invoke_sii(
        entrada, cert_bytes=None, cert_password=None, mode_override=None
    )
    record = ConsultaRecord(
        modo="unitaria",
        sii_mode=effective_mode,
        entrada=entrada,
        respuesta=respuesta,
        soap_request_xml=req_xml,
        soap_response_xml=resp_xml,
    )
    await db.consultas.insert_one(record.model_dump())
    # Upsert de la factura en `facturas_sii` con los datos canónicos extraídos
    # del XML de respuesta (importe, IVA, contraparte, etc.). De este modo la
    # vista Comparativa puede contrastar la unitaria contra el CSV comercial.
    if datos_factura:
        from router_facturas import upsert_factura  # import diferido
        await upsert_factura(
            "facturas_sii", datos_factura, "consulta_unitaria"
        )
    return record


@api_router.post("/sii/consulta-unitaria-cert", response_model=ConsultaRecord)
async def consulta_unitaria_cert(
    nif_titular: str = Form(...),
    nombre_titular: str = Form(...),
    ejercicio: str = Form(...),
    periodo: str = Form(...),
    nif_emisor: str = Form(...),
    num_serie_factura: str = Form(...),
    fecha_expedicion: str = Form(...),
    entorno: Literal[
        "preproduccion",
        "preproduccion_sello",
        "produccion",
        "produccion_sello",
    ] = Form("preproduccion"),
    nombre_emisor: Optional[str] = Form(None),
    mode: Optional[Literal["mock", "real"]] = Form(None),
    cert_password: Optional[str] = Form(None),
    certificate: Optional[UploadFile] = File(None),
):
    """Consulta unitaria multipart con certificado opcional.

    - Si ``certificate`` (PKCS#12) está presente, se fuerza modo ``real``.
    - En otro caso se usa ``mode`` o el modo por defecto del servidor.
    """
    try:
        entrada = ConsultaInput(
            nif_titular=nif_titular,
            nombre_titular=nombre_titular,
            ejercicio=ejercicio,
            periodo=periodo,
            nif_emisor=nif_emisor,
            nombre_emisor=nombre_emisor,
            num_serie_factura=num_serie_factura,
            fecha_expedicion=fecha_expedicion,
            entorno=entorno,
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors())

    cert_bytes = await _read_cert(certificate)
    respuesta, req_xml, resp_xml, effective_mode, datos_factura = await _invoke_sii(
        entrada,
        cert_bytes=cert_bytes,
        cert_password=cert_password,
        mode_override=mode,
    )
    record = ConsultaRecord(
        modo="unitaria",
        sii_mode=effective_mode,
        entrada=entrada,
        respuesta=respuesta,
        soap_request_xml=req_xml,
        soap_response_xml=resp_xml,
    )
    await db.consultas.insert_one(record.model_dump())
    if datos_factura:
        from router_facturas import upsert_factura  # import diferido
        await upsert_factura(
            "facturas_sii", datos_factura, "consulta_unitaria"
        )
    return record


# --------- Consulta batch (CSV + certificado opcional) ---------------------


def _normalize_row(row: dict) -> dict:
    """Normaliza una fila del CSV a los formatos esperados por ConsultaInput.

    Tolera variantes habituales en exportaciones de Excel / ERPs:
      - Fechas: ``YYYY-MM-DD``, ``DD/MM/YYYY``, ``D-M-YYYY`` → ``DD-MM-YYYY``.
      - Período: ``"1"`` → ``"01"``, ``"1t"`` → ``"1T"``.
      - Ejercicio: trim, recortado a 4 dígitos.
      - NIFs: trim + uppercase.
    """
    def _g(k: str) -> str:
        return (row.get(k) or "").strip()

    # Fecha — admite "/" o "-" y orden DMY o YMD
    raw_fecha = _g("fecha_expedicion")
    fecha_norm = raw_fecha
    if raw_fecha:
        parts = raw_fecha.replace("/", "-").split("-")
        if len(parts) == 3:
            a, b, c = parts
            if len(a) == 4 and a.isdigit():  # YYYY-MM-DD
                fecha_norm = f"{c.zfill(2)}-{b.zfill(2)}-{a}"
            else:  # D-M-YYYY o DD-MM-YYYY
                fecha_norm = f"{a.zfill(2)}-{b.zfill(2)}-{c.zfill(4)}"

    # Período — admite "1" → "01" y minúsculas "1t" → "1T"
    raw_periodo = _g("periodo").upper()
    if raw_periodo.isdigit() and len(raw_periodo) == 1:
        raw_periodo = raw_periodo.zfill(2)

    return {
        "nif_titular": _g("nif_titular").upper(),
        "nombre_titular": _g("nombre_titular"),
        "ejercicio": _g("ejercicio"),
        "periodo": raw_periodo,
        "nif_emisor": _g("nif_emisor").upper(),
        "nombre_emisor": _g("nombre_emisor") or None,
        "num_serie_factura": _g("num_serie_factura"),
        "fecha_expedicion": fecha_norm,
    }


def _format_validation_error(exc: Exception) -> str:
    """Convierte ValidationError en un mensaje legible."""
    if isinstance(exc, ValidationError):
        partes = []
        for err in exc.errors():
            campo = ".".join(str(p) for p in err["loc"]) or "?"
            msg = err.get("msg", "valor inválido")
            partes.append(f"{campo}: {msg}")
        return " · ".join(partes)
    return str(exc)


@api_router.post("/sii/consulta-batch", response_model=BatchResumen)
async def consulta_batch(
    file: UploadFile = File(...),
    entorno: Literal[
        "preproduccion",
        "preproduccion_sello",
        "produccion",
        "produccion_sello",
    ] = Form("preproduccion"),
    mode: Optional[Literal["mock", "real"]] = Form(None),
    cert_password: Optional[str] = Form(None),
    certificate: Optional[UploadFile] = File(None),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "El archivo debe ser un CSV")

    contents = await file.read()
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = contents.decode("latin-1")

    # Detectar separador automáticamente entre `;`, `,`, TAB y `|`.
    # Se prueba con la primera línea no vacía y se elige el carácter con más
    # apariciones (suficiente para CSVs bien formados; Sniffer falla con
    # comillas en castellano).
    first_line = next(
        (ln for ln in text.splitlines() if ln.strip()), ""
    )
    candidates = {sep: first_line.count(sep) for sep in (";", ",", "\t", "|")}
    delimiter = max(candidates, key=candidates.get) if any(candidates.values()) else ","
    logger.info(
        "CSV separador detectado: %r (apariciones=%s)",
        delimiter,
        candidates,
    )

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    expected = {
        "nif_titular",
        "nombre_titular",
        "ejercicio",
        "periodo",
        "nif_emisor",
        "num_serie_factura",
        "fecha_expedicion",
    }
    if not reader.fieldnames or not expected.issubset(
        {f.strip() for f in reader.fieldnames}
    ):
        raise HTTPException(
            400, f"Cabeceras CSV inválidas. Se esperan: {sorted(expected)}"
        )

    cert_bytes = await _read_cert(certificate)
    effective_mode = _resolve_mode(mode, bool(cert_bytes))

    # Construir cliente una vez para todo el lote (importante en real:
    # reutilizamos la sesión TLS si zeep lo permite)
    try:
        client_impl = build_client(
            effective_mode,
            cert_bytes=cert_bytes,
            cert_password=cert_password,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    batch_id = str(uuid.uuid4())
    registros: List[ConsultaRecord] = []
    errores: List[ErrorFila] = []
    contadores = {
        "Correcta": 0,
        "AceptadaConErrores": 0,
        "Anulada": 0,
        "NoRegistrada": 0,
    }

    for idx, row in enumerate(reader, start=1):
        norm = _normalize_row(row)
        try:
            entrada = ConsultaInput(entorno=entorno, **norm)
        except Exception as exc:  # noqa: BLE001
            motivo = _format_validation_error(exc)
            errores.append(ErrorFila(fila=idx, motivo=motivo, datos=norm))
            logger.warning("Fila %s inválida: %s", idx, motivo)
            continue

        try:
            respuesta, req_xml, resp_xml, _, datos_factura = await _execute_and_log(
                client_impl, entrada, effective_mode, batch_id=batch_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fila %s falló contra el SII", idx)
            errores.append(
                ErrorFila(fila=idx, motivo=f"SII: {exc}", datos=norm)
            )
            continue

        # Upsert también en facturas_sii igual que las unitarias / mensuales
        if datos_factura:
            from router_facturas import upsert_factura  # noqa: WPS433
            await upsert_factura(
                "facturas_sii", datos_factura, "consulta_batch"
            )

        record = ConsultaRecord(
            modo="batch",
            sii_mode=effective_mode,
            batch_id=batch_id,
            entrada=entrada,
            respuesta=respuesta,
            soap_request_xml=req_xml,
            soap_response_xml=resp_xml,
        )
        registros.append(record)
        contadores[respuesta.estado_factura] = (
            contadores.get(respuesta.estado_factura, 0) + 1
        )

    if registros:
        await db.consultas.insert_many([r.model_dump() for r in registros])

    return BatchResumen(
        batch_id=batch_id,
        sii_mode=effective_mode,
        total=len(registros),
        correctas=contadores["Correcta"],
        aceptadas_con_errores=contadores["AceptadaConErrores"],
        anuladas=contadores["Anulada"],
        no_registradas=contadores["NoRegistrada"],
        errores_validacion=len(errores),
        errores=errores,
        registros=registros,
    )


# --------- Consultas: listado / detalle / estadísticas ---------------------


@api_router.get("/wslogs", response_model=WsLogListResponse)
async def listar_wslogs(
    skip: int = 0,
    limit: int = 50,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    endpoint: Optional[str] = None,
    operation: Optional[str] = None,
    sii_mode: Optional[Literal["mock", "real"]] = None,
    entorno: Optional[str] = None,
    status: Optional[Literal["ok", "error"]] = None,
    nif_titular: Optional[str] = None,
    nif_emisor: Optional[str] = None,
    num_serie_factura: Optional[str] = None,
):
    """Listado paginado del log de invocaciones al WS del SII."""
    filtro: dict = {}
    rango_ts: dict = {}
    if date_from:
        rango_ts["$gte"] = date_from
    if date_to:
        # Aceptamos solo YYYY-MM-DD añadiendo final del día
        if len(date_to) == 10:
            rango_ts["$lte"] = f"{date_to}T23:59:59.999999+00:00"
        else:
            rango_ts["$lte"] = date_to
    if rango_ts:
        filtro["timestamp"] = rango_ts
    if endpoint:
        filtro["endpoint"] = {"$regex": endpoint, "$options": "i"}
    if operation:
        filtro["operation"] = operation
    if sii_mode:
        filtro["sii_mode"] = sii_mode
    if entorno:
        filtro["entorno"] = entorno
    if status:
        filtro["status"] = status
    if nif_titular:
        filtro["nif_titular"] = {"$regex": nif_titular, "$options": "i"}
    if nif_emisor:
        filtro["nif_emisor"] = {"$regex": nif_emisor, "$options": "i"}
    if num_serie_factura:
        filtro["num_serie_factura"] = {
            "$regex": num_serie_factura,
            "$options": "i",
        }

    total = await db.wslogs.count_documents(filtro)
    cursor = (
        db.wslogs.find(filtro, {"_id": 0, "request_xml": 0, "response_xml": 0})
        .sort("timestamp", -1)
        .skip(skip)
        .limit(limit)
    )
    items = await cursor.to_list(length=limit)
    # request_xml/response_xml van vacíos en el listado, se piden en /detalle
    return WsLogListResponse(total=total, items=items)


@api_router.get("/wslogs/{log_id}", response_model=WsLog)
async def obtener_wslog(log_id: str):
    doc = await db.wslogs.find_one({"id": log_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Log no encontrado")
    return doc


@api_router.get("/wslogs/stats/summary")
async def wslogs_stats():
    pipeline = [
        {
            "$group": {
                "_id": {"status": "$status", "endpoint": "$endpoint"},
                "count": {"$sum": 1},
                "avg_duration": {"$avg": "$duration_ms"},
            }
        }
    ]
    agg = await db.wslogs.aggregate(pipeline).to_list(length=200)
    return {"by_endpoint": agg}


@api_router.get("/sii/consultas", response_model=List[ConsultaRecord])
async def listar_consultas(
    skip: int = 0,
    limit: int = 50,
    modo: Optional[Literal["unitaria", "batch"]] = None,
    estado: Optional[str] = None,
    batch_id: Optional[str] = None,
):
    filtro: dict = {}
    if modo:
        filtro["modo"] = modo
    if estado:
        filtro["respuesta.estado_factura"] = estado
    if batch_id:
        filtro["batch_id"] = batch_id

    cursor = (
        db.consultas.find(filtro, {"_id": 0})
        .sort("timestamp", -1)
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


@api_router.get("/sii/consultas/{consulta_id}", response_model=ConsultaRecord)
async def obtener_consulta(consulta_id: str):
    doc = await db.consultas.find_one({"id": consulta_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Consulta no encontrada")
    return doc


@api_router.get("/sii/stats", response_model=StatsResponse)
async def estadisticas():
    pipeline = [
        {
            "$group": {
                "_id": "$respuesta.estado_factura",
                "count": {"$sum": 1},
            }
        }
    ]
    agg = await db.consultas.aggregate(pipeline).to_list(length=100)
    counts = {row["_id"]: row["count"] for row in agg}
    total = sum(counts.values())

    ultimas = (
        await db.consultas.find({}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(5)
        .to_list(length=5)
    )
    return StatsResponse(
        total=total,
        correctas=counts.get("Correcta", 0),
        aceptadas_con_errores=counts.get("AceptadaConErrores", 0),
        anuladas=counts.get("Anulada", 0),
        no_registradas=counts.get("NoRegistrada", 0),
        ultimas=ultimas,
    )


@api_router.get("/sii/csv-template")
async def csv_template():
    csv_text = (
        "nif_titular;nombre_titular;ejercicio;periodo;nif_emisor;nombre_emisor;num_serie_factura;fecha_expedicion\n"
        "B12345678;Mi Empresa S.L.;2025;01;A87654321;Proveedor Ejemplo SA;F2025-001;15-01-2025\n"
        "B12345678;Mi Empresa S.L.;2025;01;A87654321;Proveedor Ejemplo SA;F2025-002;20-01-2025\n"
    )
    return StreamingResponse(
        io.BytesIO(csv_text.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=plantilla_sii.csv"},
    )


@api_router.get("/sii/batch/{batch_id}/export")
async def exportar_batch(batch_id: str):
    registros = await db.consultas.find(
        {"batch_id": batch_id}, {"_id": 0}
    ).to_list(10000)
    if not registros:
        raise HTTPException(404, "Batch no encontrado")
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "nif_titular",
            "nombre_titular",
            "ejercicio",
            "periodo",
            "nif_emisor",
            "num_serie_factura",
            "fecha_expedicion",
            "estado_factura",
            "estado_envio",
            "codigo_error",
            "descripcion_error",
            "num_registro_presentacion",
            "csv_aeat",
            "timestamp_presentacion",
            "sii_mode",
        ]
    )
    for r in registros:
        e = r["entrada"]
        rs = r["respuesta"]
        writer.writerow(
            [
                e["nif_titular"],
                e["nombre_titular"],
                e["ejercicio"],
                e["periodo"],
                e["nif_emisor"],
                e["num_serie_factura"],
                e["fecha_expedicion"],
                rs["estado_factura"],
                rs["estado_envio"],
                rs.get("codigo_error_registro") or "",
                rs.get("descripcion_error_registro") or "",
                rs.get("num_registro_presentacion") or "",
                rs.get("csv") or "",
                rs.get("timestamp_presentacion") or "",
                r.get("sii_mode", "mock"),
            ]
        )
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=batch_{batch_id}.csv"
        },
    )


app.include_router(api_router)

# Router de gestión de facturas, CSV comercial y comparativa
from router_facturas import router as facturas_router, init as facturas_init, cleanup_orphan_jobs  # noqa: E402

facturas_init(db, logger)
app.include_router(facturas_router)

# Autenticación y administración
from router_auth import router as auth_router  # noqa: E402
from router_admin import router as admin_router  # noqa: E402
from auth_seed import seed_auth  # noqa: E402

# Los routers de auth/admin se montan también bajo /api
auth_api = APIRouter(prefix="/api")
auth_api.include_router(auth_router)
auth_api.include_router(admin_router)
app.include_router(auth_api)

# Exponer la BD en app.state para los dependencies de auth
app.state.mongo_db = db


@app.on_event("startup")
async def _startup_auth_seed():
    try:
        await seed_auth(db, logger)
    except Exception:  # noqa: BLE001
        logger.exception("seed_auth falló")


@app.on_event("startup")
async def _startup_cleanup_jobs():
    """Limpia jobs huérfanos (workers muertos por reinicio) al arrancar."""
    try:
        await cleanup_orphan_jobs()
    except Exception:  # noqa: BLE001
        logger.exception("cleanup_orphan_jobs falló")


# CORS: el frontend manda cookies HTTP-only ⇒ `allow_credentials=True`, que
# es incompatible con `origins=["*"]`. Aceptamos lista explícita del .env.
_app_url = os.environ.get("APP_URL", "").strip()
_cors_origins_env = os.environ.get("CORS_ORIGINS", "").strip()
_allow_origins: list[str]
if _cors_origins_env and _cors_origins_env != "*":
    _allow_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
elif _app_url:
    _allow_origins = [_app_url, "http://localhost:3000"]
else:
    _allow_origins = ["http://localhost:3000"]
logger.info("CORS allow_origins=%s", _allow_origins)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_allow_origins,
    allow_origin_regex=r"https?://([a-z0-9-]+\.)*emergentagent\.com|http://localhost(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Middleware: protege todas las rutas /api/* salvo las públicas de auth.
# Devuelve 401 si no hay sesión válida en cookie/Bearer.
# -----------------------------------------------------------------------------
from auth import decode_token, _extract_token, COOKIE_ACCESS  # noqa: E402

_AUTH_PUBLIC_PREFIXES = (
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/refresh",
    "/api/auth/setup/",          # check + setup
    "/api/auth/forgot-password",
)


@app.middleware("http")
async def require_auth(request, call_next):  # noqa: ANN001
    path = request.url.path or ""
    # Pasarela libre para preflight CORS, docs y rutas no-API
    if request.method == "OPTIONS" or not path.startswith("/api/"):
        return await call_next(request)
    if any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES):
        return await call_next(request)

    token = _extract_token(request)
    if not token:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "No autenticado"}, status_code=401)
    try:
        decode_token(token, "access")
    except HTTPException as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
