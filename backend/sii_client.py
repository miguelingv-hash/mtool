"""
SII SOAP client.

Implementación única `ZeepSIIClient`: invocación real al WS
ConsultaLRFactEmitidas de la AEAT con autenticación mTLS usando un
certificado PKCS#12 (.pfx/.p12).

La fábrica `build_client()` carga el certificado de la petición o, en su
defecto, del configurado en el servidor por variables de entorno.
"""

from __future__ import annotations

import hashlib  # noqa: F401  — usado por código auxiliar
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from server import ConsultaInput, RespuestaSII

logger = logging.getLogger("sii.client")


# ---------------------------------------------------------------------------
# Constantes SII
# ---------------------------------------------------------------------------

# URL pública del WSDL (informativa: se muestra en la UI y en `/api/sii/config`).
WSDL_URL = (
    "https://sede.agenciatributaria.gob.es/static_files/Sede/"
    "Procedimiento_ayuda/G417/FicherosSuministros/V_1_1/WSDL/"
    "SuministroFactEmitidas.wsdl"
)

# Ubicación local de la copia oficial del WSDL + XSDs.
# Esta carpeta se rellena en build-time (Dockerfile) o se incluye en el repo.
# Evita las 404 que devuelve la AEAT cuando zeep intenta resolver los `xsd:import`
# relativos: el WSDL vive en /WSDL/ pero los XSDs en otra ubicación distinta.
WSDL_LOCAL_DIR = Path(__file__).parent / "wsdl"
WSDL_LOCAL_FILE = WSDL_LOCAL_DIR / "SuministroFactEmitidas.wsdl"

ENDPOINTS = {
    # Pre-producción · certificado normal (persona física/jurídica/apoderado)
    "preproduccion": (
        "https://prewww1.aeat.es/wlpl/SSII-FACT/ws/fe/"
        "SiiFactFEV1SOAP"
    ),
    # Pre-producción · certificado de sello electrónico
    "preproduccion_sello": (
        "https://prewww10.aeat.es/wlpl/SSII-FACT/ws/fe/"
        "SiiFactFEV1SOAP"
    ),
    # Producción · certificado normal
    "produccion": (
        "https://www1.agenciatributaria.gob.es/wlpl/SSII-FACT/ws/fe/"
        "SiiFactFEV1SOAP"
    ),
    # Producción · certificado de sello electrónico
    "produccion_sello": (
        "https://www10.agenciatributaria.gob.es/wlpl/SSII-FACT/ws/fe/"
        "SiiFactFEV1SOAP"
    ),
}

ESTADOS_FACTURA = ["Correcta", "AceptadaConErrores", "Anulada", "NoRegistrada"]

CODIGOS_ERROR = {
    "Correcta": (None, None),
    "AceptadaConErrores": (
        "3000",
        "El NIF del destinatario no está identificado en la base de datos de la AEAT",
    ),
    "Anulada": ("1108", "Factura anulada por el suministrador"),
    "NoRegistrada": ("4102", "La factura no ha sido registrada en el SII"),
}


# ---------------------------------------------------------------------------
# XML helpers (request firmable y response simulada)
# ---------------------------------------------------------------------------


def build_soap_request_xml(entrada: "ConsultaInput") -> str:
    return f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:sii="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/ssii/fact/ws/SuministroInformacion.xsd"
                  xmlns:siiLRC="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/ssii/fact/ws/ConsultaLR.xsd">
  <soapenv:Header/>
  <soapenv:Body>
    <siiLRC:ConsultaLRFacturasEmitidas>
      <sii:Cabecera>
        <sii:IDVersionSii>1.1</sii:IDVersionSii>
        <sii:Titular>
          <sii:NombreRazon>{entrada.nombre_titular}</sii:NombreRazon>
          <sii:NIF>{entrada.nif_titular}</sii:NIF>
        </sii:Titular>
      </sii:Cabecera>
      <siiLRC:FiltroConsulta>
        <sii:PeriodoLiquidacion>
          <sii:Ejercicio>{entrada.ejercicio}</sii:Ejercicio>
          <sii:Periodo>{entrada.periodo}</sii:Periodo>
        </sii:PeriodoLiquidacion>
        <siiLRC:IDFactura>
          <sii:NumSerieFacturaEmisor>{entrada.num_serie_factura}</sii:NumSerieFacturaEmisor>
          <sii:FechaExpedicionFacturaEmisor>{entrada.fecha_expedicion}</sii:FechaExpedicionFacturaEmisor>
        </siiLRC:IDFactura>
      </siiLRC:FiltroConsulta>
    </siiLRC:ConsultaLRFacturasEmitidas>
  </soapenv:Body>
</soapenv:Envelope>"""


def build_soap_response_xml(entrada: "ConsultaInput", respuesta: "RespuestaSII") -> str:
    err_block = ""
    if respuesta.codigo_error_registro:
        err_block = (
            f"          <siiR:CodigoErrorRegistro>{respuesta.codigo_error_registro}"
            f"</siiR:CodigoErrorRegistro>\n"
            f"          <siiR:DescripcionErrorRegistro>"
            f"{respuesta.descripcion_error_registro}</siiR:DescripcionErrorRegistro>\n"
        )
    return f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:sii="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/ssii/fact/ws/SuministroInformacion.xsd"
                  xmlns:siiR="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/ssii/fact/ws/RespuestaConsultaLR.xsd">
  <soapenv:Body>
    <siiR:RespuestaConsultaLRFacturasEmitidas>
      <sii:Cabecera>
        <sii:IDVersionSii>1.1</sii:IDVersionSii>
        <sii:Titular>
          <sii:NombreRazon>{entrada.nombre_titular}</sii:NombreRazon>
          <sii:NIF>{entrada.nif_titular}</sii:NIF>
        </sii:Titular>
      </sii:Cabecera>
      <siiR:IndicadorPaginacion>NoHayMasRegistros</siiR:IndicadorPaginacion>
      <siiR:ResultadoConsulta>{respuesta.estado_envio}</siiR:ResultadoConsulta>
      <siiR:RegistroRespuestaConsultaLRFactEmitidas>
        <siiR:IDFactura>
          <sii:IDEmisorFactura>
            <sii:NIF>{entrada.nif_emisor}</sii:NIF>
          </sii:IDEmisorFactura>
          <sii:NumSerieFacturaEmisor>{entrada.num_serie_factura}</sii:NumSerieFacturaEmisor>
          <sii:FechaExpedicionFacturaEmisor>{entrada.fecha_expedicion}</sii:FechaExpedicionFacturaEmisor>
        </siiR:IDFactura>
        <siiR:DatosPresentacion>
          <siiR:NIFPresentador>{entrada.nif_titular}</siiR:NIFPresentador>
          <siiR:TimestampPresentacion>{respuesta.timestamp_presentacion}</siiR:TimestampPresentacion>
          <siiR:CSV>{respuesta.csv}</siiR:CSV>
          <siiR:NumRegistroPresentacion>{respuesta.num_registro_presentacion}</siiR:NumRegistroPresentacion>
        </siiR:DatosPresentacion>
        <siiR:EstadoFactura>
          <siiR:EstadoRegistro>{respuesta.estado_factura}</siiR:EstadoRegistro>
{err_block}        </siiR:EstadoFactura>
      </siiR:RegistroRespuestaConsultaLRFactEmitidas>
    </siiR:RespuestaConsultaLRFacturasEmitidas>
  </soapenv:Body>
</soapenv:Envelope>"""


# ---------------------------------------------------------------------------
# Cliente abstracto
# ---------------------------------------------------------------------------


class SIIClient(ABC):
    mode: str = "abstract"

    @abstractmethod
    def consultar(
        self, entrada: "ConsultaInput"
    ) -> tuple["RespuestaSII", str, str, dict | None]:
        """Devuelve (RespuestaSII, soap_request_xml, soap_response_xml,
        datos_factura).

        `datos_factura` es un dict con los campos canónicos parseados de la
        respuesta del SII (importes, IVA, contraparte, etc.) listo para
        persistir en `facturas_sii`. Es `None` cuando la AEAT no devuelve
        registro (factura NoRegistrada o error)."""


# ---------------------------------------------------------------------------
# Helper común: extracción de campos canónicos desde un registro SII
# ---------------------------------------------------------------------------


def _extraer_factura_canonica(primer, entrada) -> dict:
    """Construye un dict con los campos canónicos de una factura a partir del
    primer registro de la respuesta `ConsultaLRFacturasEmitidas` y la entrada
    original. Reutiliza `_extraer_iva_emitida` de `router_facturas` (lazy
    import para evitar la dependencia circular)."""
    from router_facturas import _extraer_iva_emitida  # noqa: WPS433

    df = (
        getattr(primer, "DatosFacturaEmitida", None)
        or getattr(primer, "DatosFactura", None)
    )
    contra = getattr(df, "Contraparte", None) if df is not None else None
    base, cuota, tipo, detalle_iva = _extraer_iva_emitida(df)
    total = getattr(df, "ImporteTotal", None) if df is not None else None
    # Fallback: la consulta MASIVA por período (ConsultaLRFacturasEmitidas)
    # NO devuelve `<ImporteTotal>` en muchos casos — típicamente para
    # facturas exentas, pero también para F1 normales. La consulta
    # INDIVIDUAL sí lo trae. Cuando la AEAT no lo manda, lo calculamos
    # como `base + cuota` (equivale a facturas.total desde el desglose IVA).
    # Esto es exacto por definición de base imponible + cuota repercutida.
    if total is None and base is not None:
        total = float(base) + float(cuota or 0)
    return {
        "num_serie_factura": entrada.num_serie_factura,
        "fecha_expedicion": entrada.fecha_expedicion,
        "nif_emisor": entrada.nif_emisor,
        "nombre_emisor": entrada.nombre_emisor,
        "ejercicio": entrada.ejercicio,
        "periodo": entrada.periodo,
        "nif_titular": entrada.nif_titular,
        "contraparte_nif": getattr(contra, "NIF", None) if contra else None,
        "contraparte_nombre": (
            getattr(contra, "NombreRazon", None) if contra else None
        ),
        "tipo_factura": getattr(df, "TipoFactura", None) if df else None,
        "clave_regimen_especial": (
            getattr(df, "ClaveRegimenEspecialOTrascendencia", None)
            if df else None
        ),
        "descripcion_operacion": (
            getattr(df, "DescripcionOperacion", None) if df else None
        ),
        "fecha_operacion": (
            str(getattr(df, "FechaOperacion", "")) or None if df else None
        ),
        "base_imponible": float(base) if base is not None else None,
        "tipo_impositivo": float(tipo) if tipo is not None else None,
        "cuota_repercutida": float(cuota) if cuota is not None else None,
        "importe_total": float(total) if total is not None else None,
        "detalle_iva": detalle_iva,
    }


# ---------------------------------------------------------------------------
# Cliente real con zeep + mTLS
# ---------------------------------------------------------------------------


class ZeepSIIClient(SIIClient):
    """Cliente SOAP real para ConsultaLRFactEmitidas con mTLS.

    Requiere un certificado PKCS#12 (.pfx/.p12) reconocido por la AEAT.
    El PFX se descifra en memoria a PEM y se escribe en archivos temporales
    durante la llamada; los archivos se borran al finalizar.

    NOTA: este cliente está completamente cableado contra el WSDL oficial
    pero requiere un certificado válido y conectividad con los endpoints de
    la AEAT para ser ejercitado.
    """

    mode = "real"

    def __init__(self, pfx_bytes: bytes, pfx_password: str):
        if not pfx_bytes:
            raise ValueError("Se requiere un archivo PKCS#12 (.pfx/.p12).")
        self._pfx_bytes = pfx_bytes
        self._pfx_password = pfx_password or ""
        # Validar el PKCS#12 cuanto antes para que la app pueda devolver
        # 400 inmediatamente (sobre todo en batch) sin procesar filas.
        self._cert_pem, self._key_pem = self._load_pem_bytes()

    def _load_pem_bytes(self) -> tuple[bytes, bytes]:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            pkcs12,
        )

        try:
            private_key, cert, additional = pkcs12.load_key_and_certificates(
                self._pfx_bytes,
                self._pfx_password.encode() if self._pfx_password else None,
            )
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                "No se pudo leer el PKCS#12. Verifica el archivo y la "
                f"contraseña: {exc}"
            ) from exc
        if private_key is None or cert is None:
            raise ValueError(
                "El PKCS#12 no contiene clave privada o certificado."
            )
        # IMPORTANTE: la AEAT rechaza con 401 si sólo se envía el certificado
        # hoja sin la cadena de CAs intermedias. Concatenamos el leaf + todos
        # los certs adicionales del PKCS#12 (CA intermedias) en el PEM, igual
        # que hace Postman/navegador.
        cert_pem = cert.public_bytes(Encoding.PEM)
        for ca in additional or []:
            cert_pem += ca.public_bytes(Encoding.PEM)
        key_pem = private_key.private_bytes(
            Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
        )
        return cert_pem, key_pem

    # ------------------------------------------------------------------
    # PFX → archivos PEM temporales (cert + key) listos para requests
    # ------------------------------------------------------------------
    def _extract_pem(self) -> tuple[str, str]:
        cert_f = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
        cert_f.write(self._cert_pem)
        cert_f.close()
        key_f = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
        key_f.write(self._key_pem)
        key_f.close()
        return cert_f.name, key_f.name

    # ------------------------------------------------------------------
    # Invocación SOAP
    # ------------------------------------------------------------------
    def consultar(self, entrada):
        # Imports diferidos para que el módulo cargue aunque zeep no esté disponible
        from lxml import etree
        from requests import Session
        from zeep import Client, Settings
        from zeep.plugins import HistoryPlugin
        from zeep.transports import Transport

        from server import RespuestaSII

        cert_path, key_path = self._extract_pem()
        history = HistoryPlugin()
        try:
            session = Session()
            session.cert = (cert_path, key_path)
            session.verify = True

            # Timeouts:
            #   timeout = handshake TLS / conexión inicial (15s).
            #   operation_timeout = read del response SOAP (45s).
            # Total <60s para no chocar contra el límite del proxy Cloudflare
            # del preview Emergent (que devuelve 5xx tras ~100s).
            transport = Transport(session=session, timeout=15, operation_timeout=45)
            settings = Settings(strict=False, xml_huge_tree=True)

            # Cargamos el WSDL desde el bundle local (file://) para evitar las
            # 404 de la AEAT en los xsd:import relativos.
            if not WSDL_LOCAL_FILE.exists():
                raise ValueError(
                    f"WSDL local no encontrado en {WSDL_LOCAL_FILE}. "
                    "Asegúrate de que la carpeta backend/wsdl está en la imagen."
                )
            wsdl_uri = WSDL_LOCAL_FILE.as_uri()
            client = Client(
                wsdl_uri,
                transport=transport,
                settings=settings,
                plugins=[history],
            )

            # Sobrescribir endpoint según el entorno seleccionado por el usuario
            binding_name = next(iter(client.wsdl.bindings.keys()))
            service = client.create_service(
                binding_name, ENDPOINTS[entrada.entorno]
            )

            cabecera = {
                "IDVersionSii": "1.1",
                "Titular": {
                    "NombreRazon": entrada.nombre_titular,
                    "NIF": entrada.nif_titular,
                },
            }
            # En ConsultaLRFacturasEmitidas el filtro NO lleva IDEmisorFactura:
            # el emisor es implícito (= el Titular, porque son facturas tuyas).
            # El tipo IDFacturaConsulta2Type sólo acepta NumSerieFacturaEmisor
            # y FechaExpedicionFacturaEmisor.
            filtro = {
                "PeriodoLiquidacion": {
                    "Ejercicio": entrada.ejercicio,
                    "Periodo": entrada.periodo,
                },
                "IDFactura": {
                    "NumSerieFacturaEmisor": entrada.num_serie_factura,
                    "FechaExpedicionFacturaEmisor": entrada.fecha_expedicion,
                },
            }

            try:
                response = service.ConsultaLRFacturasEmitidas(
                    Cabecera=cabecera, FiltroConsulta=filtro
                )
            except Exception as exc:  # noqa: BLE001
                # La AEAT devuelve HTML cuando el certificado no es válido, no
                # está autorizado para ese NIF, o el endpoint no acepta la
                # petición. Capturamos el cuerpo crudo para dar un mensaje útil.
                raw = ""
                if history.last_received:
                    try:
                        raw = etree.tostring(
                            history.last_received["envelope"],
                            pretty_print=True,
                        ).decode(errors="ignore")
                    except Exception:
                        raw = ""
                hint = _interpretar_html_aeat(raw)
                detail = f"{exc}"
                if hint:
                    detail = f"{hint}\n\n— Detalle técnico: {exc}"
                if raw:
                    detail += f"\n\n— Cuerpo devuelto (primeros 600 chars):\n{raw[:600]}"
                raise RuntimeError(detail) from exc

            # ---- Parseo de la respuesta zeep -------------------------------
            estado_envio = getattr(response, "ResultadoConsulta", "Correcto")
            registros = (
                getattr(response, "RegistroRespuestaConsultaLRFacturasEmitidas", None)
                or getattr(response, "RegistroRespuestaConsultaLRFactEmitidas", None)
                or []
            )
            primer = registros[0] if registros else None

            estado_factura = "NoRegistrada"
            cod_err = desc_err = None
            timestamp_pres = num_reg = csv_aeat = None

            if primer is not None:
                estado = getattr(primer, "EstadoFactura", None)
                if estado is not None:
                    estado_factura = (
                        getattr(estado, "EstadoRegistro", None)
                        or estado_factura
                    )
                    cod_err = getattr(estado, "CodigoErrorRegistro", None)
                    desc_err = getattr(
                        estado, "DescripcionErrorRegistro", None
                    )
                pres = getattr(primer, "DatosPresentacion", None)
                if pres is not None:
                    ts = getattr(pres, "TimestampPresentacion", None)
                    timestamp_pres = str(ts) if ts else None
                    num_reg = getattr(pres, "NumRegistroPresentacion", None)
                    csv_aeat = getattr(pres, "CSV", None)

            respuesta = RespuestaSII(
                estado_envio=estado_envio,
                estado_factura=estado_factura,
                codigo_error_registro=cod_err,
                descripcion_error_registro=desc_err,
                timestamp_presentacion=timestamp_pres,
                num_registro_presentacion=num_reg,
                csv=csv_aeat,
                endpoint=ENDPOINTS[entrada.entorno],
                wsdl=WSDL_URL,
            )

            # ---- Datos canónicos parseados ---------------------------------
            datos_factura = None
            if primer is not None and estado_factura not in ("NoRegistrada",):
                try:
                    datos_factura = _extraer_factura_canonica(primer, entrada)
                except Exception:  # noqa: BLE001
                    # Si el parseo de algún campo falla no rompemos la consulta;
                    # simplemente no persistimos en facturas_sii.
                    datos_factura = None

            # ---- XMLs crudos capturados por HistoryPlugin ------------------
            req_xml = (
                etree.tostring(history.last_sent["envelope"], pretty_print=True)
                .decode()
                if history.last_sent
                else build_soap_request_xml(entrada)
            )
            resp_xml = (
                etree.tostring(
                    history.last_received["envelope"], pretty_print=True
                ).decode()
                if history.last_received
                else build_soap_response_xml(entrada, respuesta)
            )
            return respuesta, req_xml, resp_xml, datos_factura
        finally:
            for path in (cert_path, key_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Fábrica
# ---------------------------------------------------------------------------


def server_cert_configured() -> bool:
    path = os.environ.get("SII_CERT_PATH")
    return bool(path and os.path.exists(path))


def build_client(
    cert_bytes: Optional[bytes] = None,
    cert_password: Optional[str] = None,
) -> SIIClient:
    """Construye un cliente SII (real, vía zeep + mTLS).

    - Si se aporta `cert_bytes` se usa ese certificado para la mTLS.
    - Si no, se intenta usar el certificado configurado en el servidor
      (`SII_CERT_PATH` / `SII_CERT_PASSWORD`).
    """
    if cert_bytes:
        return ZeepSIIClient(cert_bytes, cert_password or "")

    path = os.environ.get("SII_CERT_PATH")
    if not path or not os.path.exists(path):
        raise ValueError(
            "Sin certificado: aporta el PKCS#12 en la petición o configura "
            "SII_CERT_PATH en el servidor."
        )
    with open(path, "rb") as fh:
        data = fh.read()
    return ZeepSIIClient(data, os.environ.get("SII_CERT_PASSWORD", ""))


def _interpretar_html_aeat(body: str) -> str:
    """Si la AEAT devuelve HTML, intenta extraer un mensaje útil.

    Heurística sobre los textos típicos que aparecen en las páginas de error
    del portal AEAT (cl_caut, errores de certificado, apoderamiento, etc.).
    """
    if not body or "<html" not in body.lower():
        return ""
    lower = body.lower()
    if "cl_caut" in lower or "acceso denegado" in lower or "access denied" in lower:
        return (
            "La AEAT ha rechazado el acceso. Causas típicas: el certificado "
            "no está autorizado para el NIF Titular indicado, no eres "
            "apoderado/colaborador social de ese NIF, o has elegido el "
            "entorno equivocado (sello vs. normal)."
        )
    if "su certificado" in lower and ("no" in lower or "caducad" in lower):
        return (
            "Problema con el certificado: puede estar caducado, no estar "
            "instalado en el almacén correcto o no ser válido para este "
            "servicio."
        )
    if "mantenimiento" in lower or "fuera de servicio" in lower:
        return "El servicio del SII está temporalmente fuera de servicio."
    if "<title>" in lower:
        import re
        m = re.search(r"<title>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
        if m:
            return f"AEAT respondió con la página HTML: «{m.group(1).strip()[:200]}»"
    return "La AEAT devolvió una página HTML en lugar de una respuesta SOAP."
