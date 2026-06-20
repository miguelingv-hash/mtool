"""PDF generation for 'Tasas Eléctricas' with branded header on every page.

Header layout (reflecting the reference design):
  Top-left:    dark-blue square logo (placeholder text = sociedad or "Logo Empresa")
  Top-right:   red banner "Resumen de Facturación"
               dark-blue bar "AYUNTAMIENTO DE <NOMBRE>"
               dark-blue address box with `calle numero` / `CP - Provincia`
               red "Atención al cliente / <phone>"
               date in Spanish (bottom-right of header area), e.g. "2 de Mayo de 2026"
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional
from io import BytesIO
from collections import defaultdict
from datetime import datetime, timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, NextPageTemplate,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY


# --- Palette ---
BRAND_DARK = colors.HexColor("#0E4A5A")   # dark teal/blue
BRAND_RED = colors.HexColor("#E30613")    # red banner
BRAND_BG = colors.HexColor("#FFFFFF")


SPANISH_MONTHS_TITLE = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]
SPANISH_MONTHS = [m.upper() for m in SPANISH_MONTHS_TITLE]

SECTOR_LABEL = {"L1": "ELECTRICIDAD", "L2": "GAS"}


# ============= Formatting helpers =============
def fmt_eur(value: float) -> str:
    s = f"{value:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_qty(value: float) -> str:
    s = f"{value:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if "," in s:
        whole, frac = s.split(",")
        frac = frac.rstrip("0")
        s = f"{whole},{frac}" if frac else whole
    return s


def parse_anomes(anomes: str) -> tuple[int, int]:
    s = str(anomes).strip()
    return int(s[:4]), int(s[4:6])


def month_label(year: int, month: int) -> str:
    return f"MES DE {SPANISH_MONTHS[month - 1]} DE {year}"


def spanish_date(dt: datetime) -> str:
    return f"{dt.day} de {SPANISH_MONTHS_TITLE[dt.month - 1]} de {dt.year}"


# ============= CSV parsing / aggregation =============
def parse_csv_rows(content: bytes) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    text = content.decode("utf-8-sig", errors="replace")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 12:
            continue

        def num(idx: int) -> float:
            try:
                v = parts[idx].replace(".", "").replace(",", ".")
                return float(v) if v else 0.0
            except Exception:
                return 0.0

        try:
            year, month = parse_anomes(parts[2])
        except Exception:
            continue
        rows.append({
            "sociedad": parts[0], "sector": parts[1], "anomes": parts[2],
            "year": year, "month": month, "tarifa": parts[3], "codigo": parts[4],
            "cantidad": num(5), "termino_fijo": num(6), "termino_variable": num(7),
            "descuento_calidad": num(8), "alquiler": num(9), "base_atr": num(10),
            "importe_tasa": num(11),
        })
    return rows


def aggregate_by_municipio(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_codigo: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "rows": [], "total_tasa": 0.0, "min_period": None, "max_period": None,
    })
    for r in rows:
        codigo = (r.get("codigo") or "").strip()
        if not codigo:
            continue
        m = by_codigo[codigo]
        m["rows"].append(r)
        m["total_tasa"] += r["importe_tasa"]
        period = (r["year"], r["month"])
        if m["min_period"] is None or period < m["min_period"]:
            m["min_period"] = period
        if m["max_period"] is None or period > m["max_period"]:
            m["max_period"] = period
    return by_codigo


def aggregate_month_sector(rows: List[Dict[str, Any]]):
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {
        "cantidad": 0.0, "termino_fijo": 0.0, "termino_variable": 0.0,
        "alquiler": 0.0, "base_atr": 0.0, "importe_tasa": 0.0,
    })))
    for r in rows:
        b = out[(r["year"], r["month"])][r["sector"]][r["tarifa"]]
        b["cantidad"] += r["cantidad"]
        b["termino_fijo"] += r["termino_fijo"]
        b["termino_variable"] += r["termino_variable"]
        b["alquiler"] += r["alquiler"]
        b["base_atr"] += r["base_atr"]
        b["importe_tasa"] += r["importe_tasa"]
    result = []
    for ym in sorted(out.keys()):
        sectors = out[ym]
        sec_list = [(s, [(t, sectors[s][t]) for t in sorted(sectors[s].keys())])
                    for s in sorted(sectors.keys())]
        result.append((ym[0], ym[1], sec_list))
    return result


def period_label_range(min_p: tuple[int, int], max_p: tuple[int, int]) -> str:
    a = f"{SPANISH_MONTHS_TITLE[min_p[1] - 1]} {min_p[0]}"
    if min_p == max_p:
        return f"el mes de {a}"
    b = f"{SPANISH_MONTHS_TITLE[max_p[1] - 1]} {max_p[0]}"
    return f"el periodo de {a} a {b}"


# ============= Table =============
def build_table(rows_data: List[tuple]) -> Table:
    header = [
        ["", "", "IMPORTES EN EUROS", "", "", "", ""],
        ["TIPO\nTARIFA", "CONSUMO\n(kWh)", "TERMINO\nFIJO", "TERMINO\nENERGIA",
         "TOTAL\nFACTURADO", "IMPORTE\nPEAJES ABONADOS", "BASE\nT A S A S"],
    ]
    body = []
    sums = {"cantidad": 0.0, "termino_fijo": 0.0, "termino_variable": 0.0,
            "total_facturado": 0.0, "peajes": 0.0, "base_atr": 0.0}
    for tarifa, vals in rows_data:
        total_fact = vals["termino_fijo"] + vals["termino_variable"]
        peajes = max(0.0, total_fact - vals["base_atr"])
        body.append([
            tarifa, fmt_qty(vals["cantidad"]),
            fmt_eur(vals["termino_fijo"]), fmt_eur(vals["termino_variable"]),
            fmt_eur(total_fact), fmt_eur(peajes), fmt_eur(vals["base_atr"]),
        ])
        sums["cantidad"] += vals["cantidad"]
        sums["termino_fijo"] += vals["termino_fijo"]
        sums["termino_variable"] += vals["termino_variable"]
        sums["total_facturado"] += total_fact
        sums["peajes"] += peajes
        sums["base_atr"] += vals["base_atr"]
    total_row = [
        "TOTAL", fmt_qty(sums["cantidad"]),
        fmt_eur(sums["termino_fijo"]), fmt_eur(sums["termino_variable"]),
        fmt_eur(sums["total_facturado"]), fmt_eur(sums["peajes"]), fmt_eur(sums["base_atr"]),
    ]
    data = header + body + [total_row]
    col_widths = [2.0 * cm, 2.7 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 3.0 * cm, 2.5 * cm]
    t = Table(data, colWidths=col_widths, repeatRows=2)
    style = TableStyle([
        ("SPAN", (1, 0), (-2, 0)),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("LINEBELOW", (1, 0), (-2, 0), 0.5, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 8),
        ("ALIGN", (0, 1), (-1, 1), "CENTER"),
        ("VALIGN", (0, 1), (-1, 1), "MIDDLE"),
        ("BOX", (0, 1), (-1, 1), 0.75, colors.black),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.black),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("TOPPADDING", (0, 1), (-1, 1), 6),
        ("FONTNAME", (0, 2), (-1, -2), "Helvetica"),
        ("FONTSIZE", (0, 2), (-1, -2), 9),
        ("ALIGN", (0, 2), (-1, -2), "CENTER"),
        ("BOX", (0, 1), (-1, -1), 0.75, colors.black),
        ("INNERGRID", (0, 1), (-1, -1), 0.25, colors.black),
        ("TOPPADDING", (0, 2), (-1, -2), 6),
        ("BOTTOMPADDING", (0, 2), (-1, -2), 6),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 9),
        ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
        ("TOPPADDING", (0, -1), (-1, -1), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ])
    t.setStyle(style)
    return t


# ============= Branded header =============
def _address_lines(municipio: Dict[str, Any]) -> List[str]:
    name = (municipio.get("nombre") or f"AYUNTAMIENTO {municipio.get('codigo','')}").upper()
    _clean_name = name.replace("AYUNTAMIENTO DE ", "").replace("AYUNTAMIENTO ", "")
    street = " ".join(filter(None, [municipio.get("calle"), municipio.get("numero")])).strip()
    cp = (municipio.get("codigo_postal") or "").strip()
    prov = (municipio.get("provincia") or "").strip()
    if cp and prov:
        city_line = f"{cp} - {prov}"
    elif cp or prov:
        city_line = cp or prov
    else:
        city_line = ""
    return [f"AYUNTAMIENTO DE {_clean_name}"] + [s for s in [street, city_line] if s]


def _draw_simple_header(canvas, municipio: Dict[str, Any]):
    """Later pages (2+): only ayuntamiento + address, white background, left-aligned."""
    page_w, page_h = A4
    block_x = 11.5 * cm
    y = page_h - 2.0 * cm
    canvas.saveState()
    canvas.setFillColor(colors.black)
    lines = _address_lines(municipio)
    for i, line in enumerate(lines):
        canvas.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 11 if i == 0 else 10)
        canvas.drawString(block_x, y, line)
        y -= 0.55 * cm
    canvas.restoreState()


def _draw_first_page_header(canvas, municipio: Dict[str, Any], sociedad: str,
                            phone: str, logos_by_sociedad: Dict[str, str]):
    """Page 1 header: stacked top-to-bottom on the right side —
    red banner, ayuntamiento+address (white bg, left-aligned), attention phone, date.
    Logo stays at top-left.
    """
    page_w, page_h = A4
    logo_label = (logos_by_sociedad.get(sociedad) if sociedad in (logos_by_sociedad or {}) else None) \
        or (f"LOGO\n{sociedad}" if sociedad else "LOGO\nEMPRESA")

    canvas.saveState()

    # --- Logo top-left ---
    logo_x, logo_y = 2.0 * cm, page_h - 4.2 * cm
    logo_w, logo_h = 3.0 * cm, 2.8 * cm
    canvas.setFillColor(BRAND_DARK)
    canvas.rect(logo_x, logo_y, logo_w, logo_h, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    lines = logo_label.split("\n")
    line_h = 14
    ty = logo_y + logo_h / 2 + (len(lines) - 1) * line_h / 2
    for ln in lines:
        canvas.drawCentredString(logo_x + logo_w / 2, ty - 4, ln)
        ty -= line_h

    # --- Right column block ---
    right_x = 11.5 * cm
    right_w = page_w - right_x - 2.0 * cm

    # 1) Red banner "Resumen de Facturación" (TOP)
    banner_h = 0.9 * cm
    banner_y = page_h - 2.2 * cm - banner_h
    canvas.setFillColor(BRAND_RED)
    canvas.rect(right_x, banner_y, right_w, banner_h, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawCentredString(right_x + right_w / 2, banner_y + banner_h / 2 - 4,
                             "Resumen de Facturación")

    # 2) Address composition (white bg, left-aligned) — BELOW banner
    y = banner_y - 0.5 * cm
    canvas.setFillColor(colors.black)
    addr_lines = _address_lines(municipio)
    for i, line in enumerate(addr_lines):
        canvas.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 11 if i == 0 else 10)
        canvas.drawString(right_x, y, line)
        y -= 0.5 * cm

    # 3) Atención al cliente (red) — below address
    y -= 0.2 * cm
    canvas.setFillColor(BRAND_RED)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(right_x, y, f"Atención al cliente / {phone}")

    # 4) Fecha (right-aligned) — at bottom of header area
    y -= 0.7 * cm
    canvas.setFillColor(colors.black)
    canvas.setFont("Helvetica", 10)
    canvas.drawRightString(page_w - 2.0 * cm, y, spanish_date(datetime.now(timezone.utc)))

    canvas.restoreState()


# ============= Build PDF =============
def build_pdf(municipio: Dict[str, Any], rows: List[Dict[str, Any]],
              atencion_telefono: str = "900 907 000",
              sociedad: str = "",
              logos_by_sociedad: Optional[Dict[str, str]] = None) -> bytes:
    buf = BytesIO()
    # Page 1 reserves more top space for the full branded header; later pages only need
    # space for the ayuntamiento + address block.
    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=2.0 * cm, rightMargin=2.0 * cm,
        topMargin=2.0 * cm, bottomMargin=2.0 * cm,
        title=f"Tasas {municipio.get('nombre', municipio.get('codigo',''))}",
    )
    logos = logos_by_sociedad or {}

    # Frames
    first_frame = Frame(doc.leftMargin, doc.bottomMargin,
                        doc.width, doc.height - 5.5 * cm, id="first")
    later_frame = Frame(doc.leftMargin, doc.bottomMargin,
                        doc.width, doc.height, id="later")

    def on_first(canvas, _doc):
        _draw_first_page_header(canvas, municipio, sociedad, atencion_telefono, logos)

    def on_later(canvas, _doc):
        # No header on pages 2+
        pass

    doc.addPageTemplates([
        PageTemplate(id="first", frames=first_frame, onPage=on_first),
        PageTemplate(id="later", frames=later_frame, onPage=on_later),
    ])

    styles = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=styles["Normal"],
                          fontName="Helvetica", fontSize=11, leading=18,
                          alignment=TA_JUSTIFY, spaceAfter=12)
    header_intro = ParagraphStyle("HeaderIntro", parent=styles["Normal"],
                                  fontName="Helvetica", fontSize=10, alignment=TA_CENTER,
                                  spaceAfter=6, leading=14)

    story = []

    # === Cover Letter ===
    story.append(Paragraph("Estimados Sres,", body))
    story.append(Paragraph(
        "En cumplimiento de la obligación establecida por la normativa aplicable, "
        "artículo 24 de la Ley Reguladora de las Haciendas Locales, adjunto les remitimos "
        "el listado referido a la facturación de la energía suministrada en su Municipio, "
        f"correspondiente al {period_label_range(municipio['min_period'], municipio['max_period'])}.",
        body))
    story.append(Paragraph(
        "Con la remisión trimestral de estos listados entendemos cumplida, igualmente, "
        "nuestra obligación de suministro de la información necesaria a efectos de liquidación "
        "por su parte de la Tasa por Aprovechamiento Privativo del Vuelo, Suelo y/o Subsuelo.",
        body))
    story.append(Paragraph(
        f"<b>Les notificamos que el importe correspondiente a la Tasa por Aprovechamiento "
        f"Privativo del Vuelo, Suelo y/o Subsuelo, de su municipio, para el período objeto de "
        f"esta comunicación asciende a {fmt_eur(municipio['total_tasa'])} Euros.</b>",
        body))
    story.append(Paragraph(
        "Se acompaña en las siguientes páginas el detalle que soporta la liquidación del citado importe.",
        body))
    contact = municipio.get("persona_contacto") or "el Departamento de GESTIÓN FISCAL"
    phone = municipio.get("telefono_contacto") or atencion_telefono
    story.append(Paragraph(
        f"Para cualquier aclaración, rogamos se ponga en contacto con {contact}, al teléfono {phone}.",
        body))
    story.append(Paragraph(
        "Sin otro particular, quedamos a su disposición para cualquier duda o cuestión que deseen plantearnos al efecto.",
        body))
    story.append(Spacer(1, 36))
    story.append(Paragraph("Atentamente,", body))

    # === Monthly pages (one per sector per month) ===
    monthly = aggregate_month_sector(rows)
    pages = []
    for year, month, sectors in monthly:
        for sector_code, tarifas in sectors:
            pages.append((year, month, sector_code, tarifas))

    # Switch to the simpler "later" template for all subsequent pages
    story.append(NextPageTemplate("later"))

    for year, month, sector_code, tarifas in pages:
        story.append(PageBreak())
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"<b>{month_label(year, month)}</b>",
            ParagraphStyle("MesTitle", parent=styles["Normal"], alignment=TA_CENTER,
                           fontName="Helvetica-Bold", fontSize=12, spaceAfter=6),
        ))
        story.append(Paragraph(
            "EN CUMPLIMIENTO DEL REAL DECRETO 1634/2006 DE 30 DE DICIEMBRE LES FACILITAMOS EL LISTADO",
            header_intro))
        story.append(Paragraph(
            "MENSUAL A QUE SE REFIERE EL ART. 6 DE DICHA DISPOSICION",
            header_intro))
        story.append(Spacer(1, 18))
        label = SECTOR_LABEL.get(sector_code, sector_code)
        story.append(Paragraph(
            f"<b>IMPORTES EN EUROS - {label}</b>",
            ParagraphStyle("Banner", parent=styles["Normal"], alignment=TA_CENTER,
                           fontName="Helvetica-Bold", fontSize=10, spaceAfter=8)
        ))
        story.append(build_table(tarifas))

    doc.build(story)
    return buf.getvalue()
