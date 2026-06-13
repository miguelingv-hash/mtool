"""
SII Consulta API
================
Servicio FastAPI que simula la consulta del estado de facturas emitidas
contra el servicio SOAP del SII (Suministro Inmediato de Información) de la
Agencia Tributaria Española.

WSDL referencia:
https://sede.agenciatributaria.gob.es/static_files/Sede/Procedimiento_ayuda/G417/FicherosSuministros/V_1_1/WSDL/SuministroFactEmitidas.wsdl

En esta versión el servicio SOAP está MOCKEADO: las respuestas se generan de
forma determinista a partir de los datos de entrada para facilitar el
desarrollo y la integración futura con el servicio real.
"""

from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Literal
from datetime import datetime, timezone
from pathlib import Path
import os
import uuid
import csv
import io
import hashlib
import logging
import random


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="SII Consulta API", version="1.0.0")
api_router = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("sii")


# ---------------------------------------------------------------------------
# Constantes SII
# ---------------------------------------------------------------------------

WSDL_URL = (
    "https://sede.agenciatributaria.gob.es/static_files/Sede/"
    "Procedimiento_ayuda/G417/FicherosSuministros/V_1_1/WSDL/"
    "SuministroFactEmitidas.wsdl"
)

ENDPOINTS = {
    "preproduccion": (
        "https://prewww1.aeat.es/wlpl/SSII-FACT/ws/fe/"
        "ConsultaLRFactEmitidas"
    ),
    "produccion": (
        "https://www1.agenciatributaria.gob.es/wlpl/SSII-FACT/ws/fe/"
        "ConsultaLRFactEmitidas"
    ),
}

# Estados posibles de una factura según el SII
ESTADOS_FACTURA = [
    "Correcta",
    "AceptadaConErrores",
    "Anulada",
    "NoRegistrada",
]

CODIGOS_ERROR = {
    "Correcta": (None, None),
    "AceptadaConErrores": (
        "3000",
        "El NIF del destinatario no está identificado en la base de datos de la AEAT",
    ),
    "Anulada": ("1108", "Factura anulada por el suministrador"),
    "NoRegistrada": (
        "4102",
        "La factura no ha sido registrada en el SII",
    ),
}


# ---------------------------------------------------------------------------
# Modelos
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
    fecha_expedicion: str = Field(..., pattern=r"^\d{2}-\d{2}-\d{4}$")  # dd-mm-yyyy
    entorno: Literal["preproduccion", "produccion"] = "preproduccion"


class RespuestaSII(BaseModel):
    """Respuesta parseada del servicio SOAP del SII."""

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
    """Registro persistido de una consulta (entrada + respuesta + xml)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    modo: Literal["unitaria", "batch"] = "unitaria"
    batch_id: Optional[str] = None
    entrada: ConsultaInput
    respuesta: RespuestaSII
    soap_request_xml: str
    soap_response_xml: str


class BatchResumen(BaseModel):
    batch_id: str
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


# ---------------------------------------------------------------------------
# Mock del servicio SOAP
# ---------------------------------------------------------------------------


def _deterministic_estado(entrada: ConsultaInput) -> str:
    """Genera un estado de factura determinista a partir de la entrada.

    Esto permite que la misma factura consultada dos veces devuelva siempre el
    mismo estado, simulando el comportamiento real del SII.
    """
    seed = f"{entrada.nif_emisor}|{entrada.num_serie_factura}|{entrada.fecha_expedicion}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    bucket = int(digest[:4], 16) % 100
    # Distribución: 65% Correcta, 20% AceptadaConErrores, 8% Anulada, 7% NoRegistrada
    if bucket < 65:
        return "Correcta"
    if bucket < 85:
        return "AceptadaConErrores"
    if bucket < 93:
        return "Anulada"
    return "NoRegistrada"


def _build_soap_request_xml(entrada: ConsultaInput) -> str:
    return f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:sii="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/ssii/fact/ws/SuministroLR.xsd"
                  xmlns:sii1="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/ssii/fact/ws/ConsultaLR.xsd"
                  xmlns:sii2="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/ssii/fact/ws/SuministroInformacion.xsd">
  <soapenv:Header/>
  <soapenv:Body>
    <sii1:ConsultaLRFactEmitidas>
      <sii:Cabecera>
        <sii:IDVersionSii>1.1</sii:IDVersionSii>
        <sii:Titular>
          <sii2:NombreRazon>{entrada.nombre_titular}</sii2:NombreRazon>
          <sii2:NIF>{entrada.nif_titular}</sii2:NIF>
        </sii:Titular>
      </sii:Cabecera>
      <sii1:FiltroConsulta>
        <sii1:PeriodoLiquidacion>
          <sii:Ejercicio>{entrada.ejercicio}</sii:Ejercicio>
          <sii:Periodo>{entrada.periodo}</sii:Periodo>
        </sii1:PeriodoLiquidacion>
        <sii1:IDFactura>
          <sii1:IDEmisorFactura>
            <sii2:NIF>{entrada.nif_emisor}</sii2:NIF>
          </sii1:IDEmisorFactura>
          <sii1:NumSerieFacturaEmisor>{entrada.num_serie_factura}</sii1:NumSerieFacturaEmisor>
          <sii1:FechaExpedicionFacturaEmisor>{entrada.fecha_expedicion}</sii1:FechaExpedicionFacturaEmisor>
        </sii1:IDFactura>
      </sii1:FiltroConsulta>
    </sii1:ConsultaLRFactEmitidas>
  </soapenv:Body>
</soapenv:Envelope>"""


def _build_soap_response_xml(entrada: ConsultaInput, respuesta: RespuestaSII) -> str:
    err_block = ""
    if respuesta.codigo_error_registro:
        err_block = (
            f"          <sii1:CodigoErrorRegistro>{respuesta.codigo_error_registro}"
            f"</sii1:CodigoErrorRegistro>\n"
            f"          <sii1:DescripcionErrorRegistro>"
            f"{respuesta.descripcion_error_registro}</sii1:DescripcionErrorRegistro>\n"
        )
    return f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:sii1="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/ssii/fact/ws/ConsultaLR.xsd">
  <soapenv:Body>
    <sii1:RespuestaConsultaLRFactEmitidas>
      <sii1:Cabecera>
        <sii1:IDVersionSii>1.1</sii1:IDVersionSii>
        <sii1:Titular>
          <sii1:NombreRazon>{entrada.nombre_titular}</sii1:NombreRazon>
          <sii1:NIF>{entrada.nif_titular}</sii1:NIF>
        </sii1:Titular>
      </sii1:Cabecera>
      <sii1:IndicadorPaginacion>NoHayMasRegistros</sii1:IndicadorPaginacion>
      <sii1:ResultadoConsulta>{respuesta.estado_envio}</sii1:ResultadoConsulta>
      <sii1:RegistroRespuestaConsultaLRFactEmitidas>
        <sii1:IDFactura>
          <sii1:IDEmisorFactura>
            <sii1:NIF>{entrada.nif_emisor}</sii1:NIF>
          </sii1:IDEmisorFactura>
          <sii1:NumSerieFacturaEmisor>{entrada.num_serie_factura}</sii1:NumSerieFacturaEmisor>
          <sii1:FechaExpedicionFacturaEmisor>{entrada.fecha_expedicion}</sii1:FechaExpedicionFacturaEmisor>
        </sii1:IDFactura>
        <sii1:DatosPresentacion>
          <sii1:NIFPresentador>{entrada.nif_titular}</sii1:NIFPresentador>
          <sii1:TimestampPresentacion>{respuesta.timestamp_presentacion}</sii1:TimestampPresentacion>
          <sii1:CSV>{respuesta.csv}</sii1:CSV>
          <sii1:NumRegistroPresentacion>{respuesta.num_registro_presentacion}</sii1:NumRegistroPresentacion>
        </sii1:DatosPresentacion>
        <sii1:EstadoFactura>
          <sii1:EstadoRegistro>{respuesta.estado_factura}</sii1:EstadoRegistro>
{err_block}        </sii1:EstadoFactura>
      </sii1:RegistroRespuestaConsultaLRFactEmitidas>
    </sii1:RespuestaConsultaLRFactEmitidas>
  </soapenv:Body>
</soapenv:Envelope>"""


def mock_invoke_sii(entrada: ConsultaInput) -> tuple[RespuestaSII, str, str]:
    """Simula la invocación SOAP al SII y devuelve (respuesta, req_xml, resp_xml)."""
    estado_factura = _deterministic_estado(entrada)
    cod, desc = CODIGOS_ERROR[estado_factura]

    presentado = estado_factura != "NoRegistrada"
    seed = f"{entrada.nif_emisor}{entrada.num_serie_factura}"
    rnd = random.Random(seed)

    timestamp_presentacion = (
        datetime.now(timezone.utc).isoformat() if presentado else None
    )
    num_registro = (
        f"16{rnd.randint(10**13, 10**14 - 1)}" if presentado else None
    )
    csv_aeat = (
        "".join(rnd.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=16))
        if presentado
        else None
    )

    respuesta = RespuestaSII(
        estado_envio="Correcto" if estado_factura != "NoRegistrada" else "ParcialmenteCorrecto",
        estado_factura=estado_factura,
        codigo_error_registro=cod,
        descripcion_error_registro=desc,
        timestamp_presentacion=timestamp_presentacion,
        num_registro_presentacion=num_registro,
        csv=csv_aeat,
        endpoint=ENDPOINTS[entrada.entorno],
        wsdl=WSDL_URL,
    )

    req_xml = _build_soap_request_xml(entrada)
    resp_xml = _build_soap_response_xml(entrada, respuesta)
    return respuesta, req_xml, resp_xml


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@api_router.get("/")
async def root():
    return {
        "service": "SII Consulta API",
        "wsdl": WSDL_URL,
        "modo": "MOCK",
        "endpoints": ENDPOINTS,
    }


@api_router.post("/sii/consulta-unitaria", response_model=ConsultaRecord)
async def consulta_unitaria(entrada: ConsultaInput):
    respuesta, req_xml, resp_xml = mock_invoke_sii(entrada)
    record = ConsultaRecord(
        modo="unitaria",
        entrada=entrada,
        respuesta=respuesta,
        soap_request_xml=req_xml,
        soap_response_xml=resp_xml,
    )
    await db.consultas.insert_one(record.model_dump())
    return record


@api_router.post("/sii/consulta-batch", response_model=BatchResumen)
async def consulta_batch(
    file: UploadFile = File(...),
    entorno: Literal["preproduccion", "produccion"] = Form("preproduccion"),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "El archivo debe ser un CSV")
    contents = await file.read()
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = contents.decode("latin-1")

    # Detectar separador
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
    if not reader.fieldnames or not expected.issubset({f.strip() for f in reader.fieldnames}):
        raise HTTPException(
            400,
            f"Cabeceras CSV inválidas. Se esperan: {sorted(expected)}",
        )

    batch_id = str(uuid.uuid4())
    registros: List[ConsultaRecord] = []
    errores_validacion = 0
    contadores = {e: 0 for e in ESTADOS_FACTURA}

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
        except Exception as exc:
            errores_validacion += 1
            logger.warning("Fila %s inválida: %s", idx, exc)
            continue

        respuesta, req_xml, resp_xml = mock_invoke_sii(entrada)
        record = ConsultaRecord(
            modo="batch",
            batch_id=batch_id,
            entrada=entrada,
            respuesta=respuesta,
            soap_request_xml=req_xml,
            soap_response_xml=resp_xml,
        )
        registros.append(record)
        contadores[respuesta.estado_factura] += 1

    if registros:
        await db.consultas.insert_many([r.model_dump() for r in registros])

    return BatchResumen(
        batch_id=batch_id,
        total=len(registros),
        correctas=contadores["Correcta"],
        aceptadas_con_errores=contadores["AceptadaConErrores"],
        anuladas=contadores["Anulada"],
        no_registradas=contadores["NoRegistrada"],
        errores_validacion=errores_validacion,
        registros=registros,
    )


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
    """Descarga una plantilla CSV con la cabecera requerida y un ejemplo."""
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
    registros = (
        await db.consultas.find({"batch_id": batch_id}, {"_id": 0}).to_list(10000)
    )
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
