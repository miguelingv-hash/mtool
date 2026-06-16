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
):
    """Consulta mensual al SII (mock): devuelve todas las facturas del periodo
    y las inserta/actualiza en `facturas_sii`.

    En modo real esta función deberá invocar `service.ConsultaLRFacturasEmitidas`
    SIN `IDFactura` y seguir la paginación con `ClavePaginacion` hasta
    `IndicadorPaginacion == 'NoHayMasRegistros'`. Para esta iteración solo
    se entrega el mock funcional.
    """
    seed = f"{nif_titular}|{ejercicio}|{periodo}"
    n_facts = int(hashlib.md5(seed.encode()).hexdigest()[:2], 16) % 8 + 3
    facturas = [
        _mock_factura_mensual(nif_titular, nombre_titular, ejercicio, periodo, i)
        for i in range(1, n_facts + 1)
    ]
    for f in facturas:
        await upsert_factura("facturas_sii", f, "consulta_mensual")
    return {
        "total": len(facturas),
        "ejercicio": ejercicio,
        "periodo": periodo,
        "facturas": facturas,
    }


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
