"""
SII SOAP clients.

Define una interfaz común `SIIClient` con dos implementaciones:
  - MockSIIClient: respuesta determinista para desarrollo (sin certificado).
  - ZeepSIIClient: invocación real al WS ConsultaLRFactEmitidas de la AEAT
    con autenticación mTLS usando un certificado PKCS#12 (.pfx/.p12).

La fábrica `build_client()` elige la implementación según el modo solicitado
y la disponibilidad de certificado (subido en la petición o configurado por
variable de entorno).
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
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
        "https://www7.aeat.es/wlpl/SSII-FACT/ws/fe/"
        "ConsultaLRFactEmitidas"
    ),
    # Pre-producción · certificado de sello electrónico
    "preproduccion_sello": (
        "https://prewww10.aeat.es/wlpl/SSII-FACT/ws/fe/"
        "ConsultaLRFactEmitidas"
    ),
    # Producción · certificado normal
    "produccion": (
        "https://www1.agenciatributaria.gob.es/wlpl/SSII-FACT/ws/fe/"
        "ConsultaLRFactEmitidas"
    ),
    # Producción · certificado de sello electrónico
    "produccion_sello": (
        "https://www10.agenciatributaria.gob.es/wlpl/SSII-FACT/ws/fe/"
        "ConsultaLRFactEmitidas"
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


def build_soap_response_xml(entrada: "ConsultaInput", respuesta: "RespuestaSII") -> str:
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


# ---------------------------------------------------------------------------
# Cliente abstracto
# ---------------------------------------------------------------------------


class SIIClient(ABC):
    mode: str = "abstract"

    @abstractmethod
    def consultar(
        self, entrada: "ConsultaInput"
    ) -> tuple["RespuestaSII", str, str]:
        """Devuelve (RespuestaSII, soap_request_xml, soap_response_xml)."""


# ---------------------------------------------------------------------------
# Mock determinista
# ---------------------------------------------------------------------------


class MockSIIClient(SIIClient):
    """Cliente simulado que no realiza llamadas HTTP.

    El estado de cada factura se calcula con SHA-256 sobre los identificadores
    de la factura, por lo que es estable entre peticiones (misma factura ⇒
    mismo estado), igual que el comportamiento del SII real.
    """

    mode = "mock"

    def consultar(self, entrada):
        from server import RespuestaSII

        seed = (
            f"{entrada.nif_emisor}|{entrada.num_serie_factura}"
            f"|{entrada.fecha_expedicion}"
        )
        digest = hashlib.sha256(seed.encode()).hexdigest()
        bucket = int(digest[:4], 16) % 100
        if bucket < 65:
            estado_factura = "Correcta"
        elif bucket < 85:
            estado_factura = "AceptadaConErrores"
        elif bucket < 93:
            estado_factura = "Anulada"
        else:
            estado_factura = "NoRegistrada"

        cod, desc = CODIGOS_ERROR[estado_factura]
        presentado = estado_factura != "NoRegistrada"
        rnd = random.Random(f"{entrada.nif_emisor}{entrada.num_serie_factura}")

        respuesta = RespuestaSII(
            estado_envio=(
                "Correcto" if presentado else "ParcialmenteCorrecto"
            ),
            estado_factura=estado_factura,
            codigo_error_registro=cod,
            descripcion_error_registro=desc,
            timestamp_presentacion=(
                datetime.now(timezone.utc).isoformat() if presentado else None
            ),
            num_registro_presentacion=(
                f"16{rnd.randint(10**13, 10**14 - 1)}" if presentado else None
            ),
            csv=(
                "".join(
                    rnd.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=16)
                )
                if presentado
                else None
            ),
            endpoint=ENDPOINTS[entrada.entorno],
            wsdl=WSDL_URL,
        )
        return (
            respuesta,
            build_soap_request_xml(entrada),
            build_soap_response_xml(entrada, respuesta),
        )


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
    la AEAT para ser ejercitado. En desarrollo se utiliza `MockSIIClient`.
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
            private_key, cert, _additional = pkcs12.load_key_and_certificates(
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
        cert_pem = cert.public_bytes(Encoding.PEM)
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

            transport = Transport(session=session, timeout=30, operation_timeout=60)
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
                getattr(response, "RegistroRespuestaConsultaLRFactEmitidas", [])
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
            return respuesta, req_xml, resp_xml
        finally:
            for path in (cert_path, key_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Fábrica
# ---------------------------------------------------------------------------


def get_default_mode() -> str:
    """Modo por defecto del servidor (env `SII_MODE`)."""
    return os.environ.get("SII_MODE", "mock").lower()


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


def server_cert_configured() -> bool:
    path = os.environ.get("SII_CERT_PATH")
    return bool(path and os.path.exists(path))


def build_client(
    mode: Optional[str] = None,
    cert_bytes: Optional[bytes] = None,
    cert_password: Optional[str] = None,
) -> SIIClient:
    """Construye un cliente SII.

    - Si se aporta `cert_bytes` se fuerza el modo `real` con ese certificado.
    - Si no se aporta certificado y el modo es `real`, se intenta usar el
      certificado configurado en el servidor (`SII_CERT_PATH` /
      `SII_CERT_PASSWORD`).
    - En `mock` (por defecto) se usa el cliente simulado.
    """
    if cert_bytes:
        return ZeepSIIClient(cert_bytes, cert_password or "")

    effective = (mode or get_default_mode()).lower()
    if effective == "mock":
        return MockSIIClient()
    if effective == "real":
        path = os.environ.get("SII_CERT_PATH")
        if not path or not os.path.exists(path):
            raise ValueError(
                "Modo 'real' solicitado pero no hay certificado. Aporta el "
                "PKCS#12 en la petición o configura SII_CERT_PATH en el "
                "servidor."
            )
        with open(path, "rb") as fh:
            data = fh.read()
        return ZeepSIIClient(data, os.environ.get("SII_CERT_PASSWORD", ""))

    raise ValueError(f"Modo SII inválido: {mode!r}. Usa 'mock' o 'real'.")
