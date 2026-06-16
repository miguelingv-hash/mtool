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
import csv
import io
import random
import hashlib

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


# Referencias globales que se inyectan desde server.py
_db = None
_logger = None


def init(db, logger):
    global _db, _logger
    _db = db
    _logger = logger


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
                )
                log_entry["request_xml"] = req_xml
                log_entry["response_xml"] = resp_xml
                log_entry["http_status"] = 200
            except Exception as exc:  # noqa: BLE001
                log_entry["status"] = "error"
                log_entry["error_message"] = str(exc)[:2000]
                log_entry["request_xml"] = getattr(exc, "request_xml", "") or ""
                log_entry["response_xml"] = getattr(exc, "response_xml", "") or ""
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


def _consultar_mensual_real(
    client, nif_titular, nombre_titular, ejercicio, periodo, entorno
) -> tuple[list[dict], str, str]:
    """Invoca ConsultaLRFacturasEmitidas SIN IDFactura y mapea los registros
    devueltos al modelo canónico de Factura.

    Sólo se trae la **primera página** del SII (hasta 10000 registros según
    spec AEAT). Si hay paginación pendiente (`IndicadorPaginacion=ConMasRegistros`)
    NO se sigue: se devuelve lo recibido en esa primera llamada.
    Devuelve (facturas, request_xml, response_xml).
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
        transport = Transport(session=session, timeout=30, operation_timeout=60)
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
        try:
            resp = service.ConsultaLRFacturasEmitidas(
                Cabecera=cabecera, FiltroConsulta=filtro
            )
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
        for r in registros:
            idf = getattr(r, "IDFactura", None)
            df = getattr(r, "DatosFactura", None)
            contra = getattr(df, "Contraparte", None) if df else None
            desglose = getattr(df, "DesgloseFactura", None) if df else None
            base = cuota = tipo = total = None
            if desglose is not None:
                suj = getattr(desglose, "Sujeta", None)
                no_exenta = getattr(suj, "NoExenta", None) if suj else None
                desgI = (
                    getattr(no_exenta, "DesgloseIVA", None)
                    if no_exenta
                    else None
                )
                detalle = (
                    (getattr(desgI, "DetalleIVA", []) or [None])[0]
                    if desgI
                    else None
                )
                if detalle is not None:
                    base = getattr(detalle, "BaseImponible", None)
                    tipo = getattr(detalle, "TipoImpositivo", None)
                    cuota = getattr(detalle, "CuotaRepercutida", None)
            if df is not None:
                total = getattr(df, "ImporteTotal", None)
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
                }
            )
        # XML crudos (sólo primera página, sin paginación).
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
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Debe ser CSV")
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    sample = next((l for l in text.splitlines() if l.strip()), "")
    delim = max((";", ",", "\t", "|"), key=lambda c: sample.count(c))

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames or "num_serie_factura" not in {
        f.strip() for f in reader.fieldnames
    }:
        raise HTTPException(
            400,
            "Cabeceras inválidas. La columna 'num_serie_factura' es obligatoria. "
            "Descarga la plantilla con /api/comercial/csv-template.",
        )

    total = 0
    errores = []
    for idx, row in enumerate(reader, start=1):
        norm = normalize_factura_row(row)
        if not norm.get("num_serie_factura"):
            errores.append({"fila": idx, "motivo": "num_serie_factura vacío"})
            continue
        try:
            FacturaDatos(**norm)  # validación
        except Exception as e:  # noqa: BLE001
            errores.append({"fila": idx, "motivo": str(e)})
            continue
        await upsert_factura("facturas_comercial", norm, "csv_comercial")
        total += 1
    return {"total": total, "errores": errores}


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


@router.get("/comparativa")
async def comparativa(
    skip: int = 0,
    limit: int = 200,
    only_diffs: bool = True,
):
    """Compara facturas SII vs Comercial por `num_serie_factura`.

    Devuelve para cada nº de factura:
      - presente en SII / en Comercial / en ambas
      - lista de campos con diferencias (si están en ambas)
    """
    sii_docs = await _db.facturas_sii.find(
        {}, {"_id": 0, "versiones": 0}
    ).to_list(length=10000)
    com_docs = await _db.facturas_comercial.find(
        {}, {"_id": 0, "versiones": 0}
    ).to_list(length=10000)

    sii_map = {d["num_serie_factura"]: d for d in sii_docs}
    com_map = {d["num_serie_factura"]: d for d in com_docs}
    todas = sorted(set(sii_map.keys()) | set(com_map.keys()))

    resultados = []
    for ns in todas:
        sii = sii_map.get(ns)
        com = com_map.get(ns)
        if sii and com:
            d = diff_facturas(sii, com)
            estado = "coincide" if not d else "discrepancia"
            resultados.append(
                {
                    "num_serie_factura": ns,
                    "estado": estado,
                    "en_sii": True,
                    "en_comercial": True,
                    "diferencias": d,
                    "sii": sii,
                    "comercial": com,
                }
            )
        elif sii:
            resultados.append(
                {
                    "num_serie_factura": ns,
                    "estado": "solo_sii",
                    "en_sii": True,
                    "en_comercial": False,
                    "diferencias": {},
                    "sii": sii,
                    "comercial": None,
                }
            )
        else:
            resultados.append(
                {
                    "num_serie_factura": ns,
                    "estado": "solo_comercial",
                    "en_sii": False,
                    "en_comercial": True,
                    "diferencias": {},
                    "sii": None,
                    "comercial": com,
                }
            )

    if only_diffs:
        resultados = [
            r for r in resultados if r["estado"] != "coincide"
        ]

    return {
        "total": len(resultados),
        "campos_canonicos": CAMPOS_CANONICOS,
        "campos_numericos": CAMPOS_NUMERICOS,
        "items": resultados[skip : skip + limit],
    }
