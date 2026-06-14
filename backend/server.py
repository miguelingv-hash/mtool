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
load_dotenv(ROOT_DIR / ".env")

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
    entorno: Literal["preproduccion", "produccion"] = "preproduccion"


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


class BatchResumen(BaseModel):
    batch_id: str
    sii_mode: str
    total: int
    correctas: int
    aceptadas_con_errores: int
    anuladas: int
    no_registradas: int
    errores_validacion: int
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
        respuesta, req_xml, resp_xml = client_impl.consultar(entrada)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error invocando SII (%s)", effective_mode)
        raise HTTPException(502, f"Error en servicio SII: {exc}")

    return respuesta, req_xml, resp_xml, effective_mode


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
    respuesta, req_xml, resp_xml, effective_mode = await _invoke_sii(
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
    entorno: Literal["preproduccion", "produccion"] = Form("preproduccion"),
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
    respuesta, req_xml, resp_xml, effective_mode = await _invoke_sii(
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
    return record


# --------- Consulta batch (CSV + certificado opcional) ---------------------


@api_router.post("/sii/consulta-batch", response_model=BatchResumen)
async def consulta_batch(
    file: UploadFile = File(...),
    entorno: Literal["preproduccion", "produccion"] = Form("preproduccion"),
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

    sample = text[:1024]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","

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
    errores_validacion = 0
    contadores = {
        "Correcta": 0,
        "AceptadaConErrores": 0,
        "Anulada": 0,
        "NoRegistrada": 0,
    }

    for idx, row in enumerate(reader, start=1):
        try:
            entrada = ConsultaInput(
                nif_titular=row.get("nif_titular", "").strip(),
                nombre_titular=row.get("nombre_titular", "").strip(),
                ejercicio=row.get("ejercicio", "").strip(),
                periodo=row.get("periodo", "").strip(),
                nif_emisor=row.get("nif_emisor", "").strip(),
                nombre_emisor=(row.get("nombre_emisor") or "").strip() or None,
                num_serie_factura=row.get("num_serie_factura", "").strip(),
                fecha_expedicion=row.get("fecha_expedicion", "").strip(),
                entorno=entorno,
            )
        except Exception as exc:  # noqa: BLE001
            errores_validacion += 1
            logger.warning("Fila %s inválida: %s", idx, exc)
            continue

        try:
            respuesta, req_xml, resp_xml = client_impl.consultar(entrada)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fila %s falló contra el SII", idx)
            errores_validacion += 1
            continue

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
        errores_validacion=errores_validacion,
        registros=registros,
    )


# --------- Consultas: listado / detalle / estadísticas ---------------------


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

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
