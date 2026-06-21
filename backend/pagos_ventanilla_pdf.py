"""
pagos_ventanilla_pdf.py — Generación de "Documento de pago por Ventanilla".

Soporta 2 sociedades emisoras: `TTE` (TotalEnergies Clientes, S.A.U.) y
`BASER` (Baser Comercializadora de Referencia, S.A.). Recibe una lista de filas
del CSV (una por documento de pago) y produce un PDF por fila. Cada PDF tiene
dos resguardos (cliente + entidad colaboradora) + código QR + código de barras
estándar Cuaderno 57 código 507.

CSV (separador `;`, decimales con coma o punto):
  sociedad;nombre_cliente;cif_nif;direccion_social;direccion_suministro;
  cuenta_contrato;numero_factura;fecha_emision_factura;fecha_emision_doc;
  fecha_limite_pago;importe;validez_meses;sufijo;idioma

Valores aceptados:
  sociedad  → "TTE" | "BASER"
  fechas    → DD/MM/YYYY  ó  DD.MM.YYYY  ó  YYYY-MM-DD
  importe   → "295,64" ó "295.64"
  sufijo    → "510" (sin recobro), "511"/"512" (con recobro)
  validez_meses → entero (5 por defecto)
  idioma    → "es" (único soportado por ahora)
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import qrcode
from barcode import Code128
from barcode.writer import ImageWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Sociedades
ASSETS_DIR = Path("/app/backend/assets/logos")

SOCIEDADES = {
    "TTE": {
        "name_display": "TotalEnergies Clientes",
        "logo_path": ASSETS_DIR / "tte.png",
        "name_legal": "TotalEnergies Clientes, S.A.U.",
        "address": "Plaza de los Ferroviarios Asturianos 1, 33012 Oviedo (España)",
        "registral": "Reg. M. Asturias, T. 4.526, F. 186, H. AS-60.297, CIF A-95000295",
        "cif_full": "A-95000295",
        "cif_digits": "95000295",
        "telefono": "900 907 000",
        "whatsapp": "678 244 222",
        "website": "totalenergies.es",
        "qr_url": "https://www.totalenergies.es/es/hogares/atencion-al-cliente/pagar-facturas",
        "entidades": ["UNICAJA BANCO", "BBVA", "SANTANDER"],
        "primary_color": colors.HexColor("#003366"),  # azul TotalEnergies
        "accent_color": colors.HexColor("#E60028"),   # rojo
    },
    "BASER": {
        "name_display": "BASER COR de TotalEnergies",
        "logo_path": ASSETS_DIR / "baser.png",
        "name_legal": "BASER COMERCIALIZADORA DE REFERENCIA S.A.",
        "address": "Plaza de los Ferroviarios Asturianos 1, 33012 - Oviedo (España)",
        "registral": "Reg. M. Asturias, T. 3.777, F. 175, H. AS-39.440, CIF A-74251836",
        "cif_full": "A-74251836",
        "cif_digits": "74251836",
        "telefono": "900 902 947",
        "whatsapp": "678 270 027",
        "website": "basercor.es",
        "qr_url": "https://www.basercor.es/es/descarga-de-documento-de-pago.html",
        "entidades": ["CORREOS", "UNICAJA BANCO", "BBVA", "SANTANDER"],
        "primary_color": colors.HexColor("#0066A1"),  # azul Baser
        "accent_color": colors.HexColor("#E60028"),
    },
}

# Para Baser: si importe >= 999,99 € se elimina "CORREOS" (Ley 11/2021)
LIMITE_CORREOS = 999.99

CSV_COLUMNS = [
    "sociedad", "nombre_cliente", "cif_nif", "direccion_social",
    "direccion_suministro", "cuenta_contrato", "numero_factura",
    "fecha_emision_factura", "fecha_emision_doc", "fecha_limite_pago",
    "importe", "validez_meses", "sufijo", "idioma",
]


# =============================================================================
# Helpers
# =============================================================================
def _parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha con formato no soportado: {s!r}")


def _parse_decimal(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    # quitar separador de miles cuando hay ambos
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return float(s)


def _add_months(d: date, months: int) -> date:
    """Suma `months` meses a `d` ajustando overflow de fin de mes."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last_day = (date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last_day))


def _fmt_date(d: Optional[date]) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def _fmt_amount(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


# =============================================================================
# CSV parsing
# =============================================================================
def parse_csv_rows(raw: bytes) -> List[Dict[str, Any]]:
    """Lee CSV con cabecera o sin ella (posicional)."""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=";", quotechar='"')
    rows = [r for r in reader if r and any((c or "").strip() for c in r)]
    if not rows:
        return []
    # ¿tiene cabecera?
    first = [c.strip().lower() for c in rows[0]]
    if "sociedad" in first and "numero_factura" in first:
        header = first
        data_rows = rows[1:]
    else:
        header = CSV_COLUMNS
        data_rows = rows
    out: List[Dict[str, Any]] = []
    for idx, r in enumerate(data_rows, start=1):
        # tolerar filas más cortas
        d = {h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header)}
        try:
            sociedad = (d.get("sociedad") or "").upper().strip()
            if sociedad not in SOCIEDADES:
                raise ValueError(f"Sociedad desconocida: {sociedad!r} (debe ser TTE o BASER)")
            row = {
                "sociedad": sociedad,
                "nombre_cliente": (d.get("nombre_cliente") or "").strip(),
                "cif_nif": (d.get("cif_nif") or "").strip().upper(),
                "direccion_social": (d.get("direccion_social") or "").strip(),
                "direccion_suministro": (d.get("direccion_suministro") or "").strip(),
                "cuenta_contrato": (d.get("cuenta_contrato") or "").strip(),
                "numero_factura": (d.get("numero_factura") or "").strip(),
                "fecha_emision_factura": _parse_date(d.get("fecha_emision_factura", "")),
                "fecha_emision_doc": _parse_date(d.get("fecha_emision_doc", "")) or date.today(),
                "fecha_limite_pago": _parse_date(d.get("fecha_limite_pago", "")),
                "importe": _parse_decimal(d.get("importe", "0")),
                "validez_meses": int(d.get("validez_meses") or 5),
                "sufijo": (d.get("sufijo") or "510").strip(),
                "idioma": (d.get("idioma") or "es").strip().lower(),
                "_row_index": idx,
            }
        except Exception as e:
            raise ValueError(f"Fila {idx} inválida: {e}")
        if not row["nombre_cliente"] or not row["numero_factura"] or row["importe"] <= 0:
            raise ValueError(f"Fila {idx}: faltan datos obligatorios (nombre_cliente, numero_factura, importe)")
        out.append(row)
    return out


# =============================================================================
# Código de barras Cuaderno 57 — Código 507 (Cobro por ventanilla)
# Estructura: 90507 + CIF(8) + SUFIJO(3) + IDENTIFICACION(6=DDMMYY) +
#             REFERENCIA(13) + IMPORTE(10 céntimos) + 0
# =============================================================================
def _checksum_2digits(s: str) -> str:
    """Cálculo de 2 dígitos de control mod 97 sobre la cadena `s`."""
    n = int(re.sub(r"\D", "", s) or "0")
    return f"{(98 - (n * 100) % 97) % 97:02d}"


def build_referencia(row: Dict[str, Any]) -> str:
    """Construye la referencia de 13 dígitos = 11 dígitos + 2 control.

    Por defecto codifica como YYA + numerico(8). El campo `numero_factura`
    puede ser alfanumérico, por lo que extraemos su parte numérica.
    """
    año = row["fecha_emision_factura"] or row["fecha_emision_doc"]
    yy = f"{año.year % 100:02d}"
    # Codificar serie como 1 dígito a partir del 1er char alfanumérico de la factura
    numf = (row["numero_factura"] or "").strip()
    serie_char = next((c for c in numf if c.isalpha()), "")
    serie_digit = str(ord(serie_char.upper()) - ord("A") + 1) if serie_char else "0"
    serie_digit = serie_digit[-1]  # un solo dígito
    # Numérico de la factura (últimos 8 dígitos)
    digits = re.sub(r"\D", "", numf)[-8:].rjust(8, "0")
    base = f"{yy}{serie_digit}{digits}"  # 11 dígitos
    base = base[:11].rjust(11, "0")
    return base + _checksum_2digits(base)


def build_barcode_string(row: Dict[str, Any], soc: Dict[str, Any], fecha_validez: date) -> str:
    """Cadena completa del código de barras (46 dígitos)."""
    cif8 = soc["cif_digits"].rjust(8, "0")[-8:]
    sufijo = str(row["sufijo"] or "510").rjust(3, "0")[-3:]
    ident = fecha_validez.strftime("%d%m%y")
    referencia = build_referencia(row)
    importe_cents = int(round(row["importe"] * 100))
    importe_str = f"{importe_cents:010d}"
    return f"90507{cif8}{sufijo}{ident}{referencia}{importe_str}0"


def _render_barcode_png(code: str) -> bytes:
    """Renderiza Code128 a PNG bytes."""
    buf = BytesIO()
    writer = ImageWriter()
    writer.set_options({"module_height": 12.0, "font_size": 10, "text_distance": 3.0,
                        "quiet_zone": 2.0, "write_text": True})
    Code128(code, writer=writer).write(buf, options={"module_height": 12.0, "font_size": 10})
    return buf.getvalue()


def _render_qr_png(url: str) -> bytes:
    """Genera QR PNG bytes."""
    img = qrcode.make(url, box_size=6, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# =============================================================================
# PDF builder
# =============================================================================
@dataclass
class PageGeom:
    width: float = A4[0]
    height: float = A4[1]
    margin_x: float = 15 * mm
    margin_y: float = 15 * mm


def build_pdf(row: Dict[str, Any], logos_by_sociedad: Optional[Dict[str, bytes]] = None) -> bytes:
    """Genera el PDF de un documento de pago a partir de una fila parseada."""
    soc = SOCIEDADES[row["sociedad"]]
    geom = PageGeom()
    buf = BytesIO()
    c = Canvas(buf, pagesize=A4)

    # Calcular fecha de validez = fecha_emision_doc + validez_meses
    fecha_validez = _add_months(row["fecha_emision_doc"], int(row["validez_meses"] or 5))

    # Entidades colaboradoras (filtrar CORREOS si Baser e importe >= límite)
    entidades = list(soc["entidades"])
    if row["sociedad"] == "BASER" and row["importe"] >= LIMITE_CORREOS:
        entidades = [e for e in entidades if e != "CORREOS"]

    # Cadena de código de barras y código de referencia visible
    bc_string = build_barcode_string(row, soc, fecha_validez)
    referencia_visible = build_referencia(row)

    # ===========================================================================
    # CABECERA — logo + título
    # ===========================================================================
    y = geom.height - geom.margin_y
    # Logo (si existe el PNG, se dibuja; si no, fallback a texto estilizado)
    logo_path = soc.get("logo_path")
    logo_h = 40  # altura fija
    if logo_path and Path(logo_path).exists():
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(str(logo_path))
            iw, ih = img.getSize()
            logo_w = logo_h * (iw / ih)
            c.drawImage(img, geom.margin_x, y - logo_h, logo_w, logo_h, mask="auto",
                        preserveAspectRatio=True)
        except Exception:
            c.setFillColor(soc["primary_color"])
            c.setFont("Helvetica-Bold", 22)
            c.drawString(geom.margin_x, y - 10, soc["name_display"])
            c.setFillColor(colors.black)
    else:
        c.setFillColor(soc["primary_color"])
        c.setFont("Helvetica-Bold", 22)
        c.drawString(geom.margin_x, y - 10, soc["name_display"])
        c.setFillColor(colors.black)

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(geom.width - geom.margin_x, y - 10, "Documento para pago por ventanilla")

    # Sub-cabecera: contacto
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#555"))
    sub = f"Línea {soc['name_display'].split(' de ')[0]}  ·  {soc['telefono']}  ·  WhatsApp {soc['whatsapp']}  ·  {soc['website']}"
    c.drawString(geom.margin_x, y - 28, sub)
    c.setFillColor(colors.black)

    # Línea separadora
    c.setStrokeColor(soc["primary_color"])
    c.setLineWidth(1.2)
    c.line(geom.margin_x, y - 34, geom.width - geom.margin_x, y - 34)

    # ===========================================================================
    # COLUMNA IZQUIERDA — datos cliente + suministro + datos pago
    # ===========================================================================
    left_x = geom.margin_x
    col_width = (geom.width - 2 * geom.margin_x) * 0.58
    box_top = y - 42
    box_y = box_top

    def section_title(txt, x, y0):
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(soc["primary_color"])
        c.drawString(x, y0, txt)
        c.setFillColor(colors.black)
        return y0 - 12

    def field(label, value, x, y0, label_w=85):
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor("#555"))
        c.drawString(x, y0, f"{label}:")
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + label_w, y0, str(value or "—"))
        return y0 - 12

    box_y = section_title("Datos del cliente", left_x, box_y)
    box_y = field("Nombre / Razón social", row["nombre_cliente"], left_x, box_y, 130)
    box_y = field("Dirección", row["direccion_social"], left_x, box_y, 130)
    box_y = field("C.I.F. / N.I.F.", row["cif_nif"], left_x, box_y, 130)
    box_y -= 6

    box_y = section_title("Datos del suministro", left_x, box_y)
    box_y = field("Dirección", row["direccion_suministro"], left_x, box_y, 130)
    box_y = field("Cuenta contrato", row["cuenta_contrato"], left_x, box_y, 130)
    box_y -= 6

    box_y = section_title("Datos para pago", left_x, box_y)
    box_y = field("Referencia", referencia_visible, left_x, box_y, 130)
    box_y = field("Nº de factura", row["numero_factura"], left_x, box_y, 130)
    box_y = field("Fecha emisión factura", _fmt_date(row["fecha_emision_factura"]),
                  left_x, box_y, 130)
    # Importe destacado
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#555"))
    c.drawString(left_x, box_y, "Importe (€):")
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(soc["primary_color"])
    c.drawString(left_x + 130, box_y - 2, _fmt_amount(row["importe"]))
    c.setFillColor(colors.black)
    box_y -= 22

    # Fechas (emisión doc + límite)
    box_y -= 4
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#555"))
    c.drawString(left_x, box_y, "Fecha de emisión de este documento:")
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(colors.black)
    c.drawString(left_x + 170, box_y, _fmt_date(row["fecha_emision_doc"]))
    box_y -= 12
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#555"))
    c.drawString(left_x, box_y, "Fecha límite de pago:")
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(soc["accent_color"])
    c.drawString(left_x + 170, box_y, _fmt_date(row["fecha_limite_pago"]))
    c.setFillColor(colors.black)
    box_y -= 16

    # Frase roja
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(soc["accent_color"])
    legal = (f"Una vez superada la fecha límite de pago indicada arriba, si usted desea pagar "
             f"puede utilizar este mismo documento en las entidades colaboradoras "
             f"durante un plazo de {row['validez_meses']} meses desde su emisión.")
    _wrapped_text(c, legal, left_x, box_y, col_width, 9)
    c.setFillColor(colors.black)
    box_y -= 30

    # ===========================================================================
    # COLUMNA DERECHA — instrucciones de pago + QR + entidades bancarias
    # ===========================================================================
    right_x = left_x + col_width + 10
    right_w = geom.width - geom.margin_x - right_x
    ry = box_top
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(soc["primary_color"])
    c.drawString(right_x, ry, "Pague fácilmente sus facturas pendientes")
    c.setFillColor(colors.black)
    ry -= 14
    c.setFont("Helvetica", 8.5)
    bullets = [
        "Con tarjeta o Bizum:",
        f"  · Escaneando el código QR.",
        f"  · En el Área de Cliente o web {soc['website']}.",
        f"  · Por WhatsApp al {soc['whatsapp']}.",
        f"  · Llamando gratuitamente al {soc['telefono']}.",
        "",
        "En efectivo con este documento en:",
    ]
    for b in bullets:
        c.drawString(right_x, ry, b); ry -= 11
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(right_x, ry, ", ".join(entidades) + ".")
    ry -= 18

    # QR (link a web pública con número de factura)
    qr_url = f"{soc['qr_url']}?fc1={row['numero_factura']}&cp=docpago"
    qr_png = _render_qr_png(qr_url)
    qr_img = _imagereader(qr_png)
    qr_size = 70
    c.drawImage(qr_img, right_x, ry - qr_size, qr_size, qr_size, mask="auto")
    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(colors.HexColor("#555"))
    c.drawString(right_x + qr_size + 8, ry - qr_size + 50,
                 "Escanee el QR para pagar")
    c.drawString(right_x + qr_size + 8, ry - qr_size + 38,
                 "con tarjeta o Bizum.")
    c.setFillColor(colors.black)
    ry = ry - qr_size - 10

    # ===========================================================================
    # SECCIÓN INFERIOR — Resguardo cliente + Resguardo entidad colaboradora
    # ===========================================================================
    # Línea separadora gruesa
    sep_y = max(box_y, ry) - 4
    c.setStrokeColor(colors.HexColor("#999"))
    c.setDash(2, 3)
    c.line(geom.margin_x, sep_y, geom.width - geom.margin_x, sep_y)
    c.setDash()  # reset

    # Resguardo cliente (izquierda) + Resguardo entidad colaboradora (derecha)
    rg_y = sep_y - 14
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(soc["primary_color"])
    c.drawString(left_x, rg_y, "Resguardo para el cliente")
    c.drawString(right_x, rg_y, "Resguardo para la entidad colaboradora")
    c.setFillColor(colors.black)
    rg_y -= 14

    # Resumen cliente
    lc = rg_y
    c.setFont("Helvetica", 8.5)
    lc = _kv(c, "Validez del documento", _fmt_date(fecha_validez), left_x, lc, 110)
    lc = _kv(c, "Referencia", referencia_visible, left_x, lc, 110)
    lc = _kv(c, "Nº de factura", row["numero_factura"], left_x, lc, 110)
    lc = _kv(c, "Importe (€)", _fmt_amount(row["importe"]), left_x, lc, 110)
    lc = _kv(c, "Nombre/Razón social", row["nombre_cliente"], left_x, lc, 110)

    # Resumen entidad colaboradora
    rc = rg_y
    rc = _kv(c, "Validez del documento", _fmt_date(fecha_validez), right_x, rc, 110)
    rc = _kv(c, "Entidad emisora", soc["cif_digits"], right_x, rc, 110)
    rc = _kv(c, "Sufijo", row["sufijo"], right_x, rc, 110)
    rc = _kv(c, "Identificación", fecha_validez.strftime("%d%m%y"), right_x, rc, 110)
    rc = _kv(c, "Referencia", referencia_visible, right_x, rc, 110)
    rc = _kv(c, "Importe (€)", _fmt_amount(row["importe"]), right_x, rc, 110)
    rc = _kv(c, "Nombre/Razón social", row["nombre_cliente"], right_x, rc, 110)

    # ===========================================================================
    # CÓDIGO DE BARRAS (abajo, full width)
    # ===========================================================================
    bc_png = _render_barcode_png(bc_string)
    bc_img = _imagereader(bc_png)
    bc_w = geom.width - 2 * geom.margin_x
    bc_h = 32
    bc_y = geom.margin_y + 6
    c.drawImage(bc_img, geom.margin_x, bc_y, bc_w, bc_h, mask="auto", preserveAspectRatio=False)

    # Pie con datos registrales
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#666"))
    c.drawString(geom.margin_x, geom.margin_y - 4,
                 f"{soc['name_legal']} · {soc['address']} · {soc['registral']}")
    c.setFillColor(colors.black)

    c.showPage()
    c.save()
    return buf.getvalue()


def _kv(c: Canvas, label: str, value: str, x: float, y: float, lw: int = 100) -> float:
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#555"))
    c.drawString(x, y, f"{label}:")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(x + lw, y, str(value or "—"))
    return y - 11


def _wrapped_text(c: Canvas, text: str, x: float, y: float, max_w: float, leading: int = 10):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    words = text.split()
    line = ""
    for w in words:
        candidate = f"{line} {w}".strip()
        if stringWidth(candidate, "Helvetica-Oblique", 8) > max_w:
            c.drawString(x, y, line)
            y -= leading
            line = w
        else:
            line = candidate
    if line:
        c.drawString(x, y, line)


def _imagereader(png_bytes: bytes):
    from reportlab.lib.utils import ImageReader
    return ImageReader(BytesIO(png_bytes))
