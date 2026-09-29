"""PDF document generator (reports, kwitansi, invoice)."""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape as landscape_page
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from modules.siabumdes.adapters.external.report_formatting import format_money, is_money_header, row_is_total
from modules.siabumdes.report_branding import ReportBranding, fetch_logo_bytes, get_report_branding

_is_money_header = is_money_header
_row_is_total = row_is_total


def _styles(primary_hex: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    primary = colors.HexColor(f"#{primary_hex}")
    return {
        "org": ParagraphStyle(
            "OrgName", parent=base["Heading1"], alignment=TA_CENTER, fontSize=13,
            textColor=primary, spaceAfter=2, leading=16,
        ),
        "legal": ParagraphStyle(
            "OrgLegal", parent=base["Normal"], alignment=TA_CENTER, fontSize=8,
            textColor=colors.HexColor("#444444"), spaceAfter=1,
        ),
        "addr": ParagraphStyle(
            "OrgAddr", parent=base["Normal"], alignment=TA_CENTER, fontSize=7.5,
            textColor=colors.HexColor("#555555"), leading=10,
        ),
        "title": ParagraphStyle(
            "DocTitle", parent=base["Heading1"], alignment=TA_CENTER, fontSize=12, spaceBefore=8, spaceAfter=4
        ),
        "subtitle": ParagraphStyle(
            "DocSub", parent=base["Normal"], alignment=TA_CENTER, fontSize=9,
            textColor=colors.HexColor("#444444"), spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "DocMeta", parent=base["Normal"], alignment=TA_CENTER, fontSize=7.5,
            textColor=colors.HexColor("#666666"), spaceAfter=8,
        ),
        "body": ParagraphStyle("DocBody", parent=base["Normal"], fontSize=10, leading=14),
        "sign_role": ParagraphStyle(
            "SignRole", parent=base["Normal"], alignment=TA_CENTER, fontSize=8.5, fontName="Helvetica-Bold"
        ),
        "sign_name": ParagraphStyle(
            "SignName", parent=base["Normal"], alignment=TA_CENTER, fontSize=8.5, spaceBefore=28
        ),
        "right": ParagraphStyle("DocRight", parent=base["Normal"], alignment=TA_RIGHT, fontSize=10),
        "left": ParagraphStyle("DocLeft", parent=base["Normal"], alignment=TA_LEFT, fontSize=10),
        "footer": ParagraphStyle(
            "DocFooter", parent=base["Normal"], alignment=TA_CENTER, fontSize=7.5,
            textColor=colors.HexColor("#777777"),
        ),
    }


def generate_pdf_report_platypus(
    *,
    title: str,
    subtitle: str = "",
    lines: Optional[list[str]] = None,
    table_headers: Optional[list[str]] = None,
    table_rows: Optional[list[list[Any]]] = None,
    footer: str = "",
    landscape: bool = False,
    branding: Optional[ReportBranding] = None,
    generated_by: str = "",
) -> bytes:
    """Letterhead + signatures ReportLab renderer (WeasyPrint fallback / simple docs)."""
    branding = branding or get_report_branding()
    primary = branding.primary_color
    page = landscape_page(A4) if landscape else A4
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=page,
        leftMargin=1.4 * cm,
        rightMargin=1.4 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.4 * cm,
    )
    s = _styles(primary)
    story: list[Any] = []

    logo_bytes = fetch_logo_bytes(branding.logo_url) if branding.logo_url else None
    if logo_bytes:
        try:
            logo_img = Image(io.BytesIO(logo_bytes))
            logo_img.drawHeight = 1.5 * cm
            logo_img.drawWidth = logo_img.drawHeight * (logo_img.imageWidth / logo_img.imageHeight)
            logo_img.hAlign = "CENTER"
            story.append(logo_img)
            story.append(Spacer(1, 1.5 * mm))
        except Exception:  # noqa: BLE001 -- a broken/unsupported logo must never break the report
            pass

    story.append(Paragraph(branding.org_name, s["org"]))
    story.append(Paragraph(branding.org_legal_name, s["legal"]))
    if branding.address_block:
        story.append(Paragraph(branding.address_block.replace("\n", "<br/>"), s["addr"]))
    story.append(Spacer(1, 2 * mm))
    line = Table([[""]], colWidths=[doc.width])
    line.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 2, colors.HexColor(f"#{primary}"))]))
    story.append(line)
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph(title, s["title"]))
    if subtitle:
        story.append(Paragraph(subtitle, s["subtitle"]))
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    meta = f"Dicetak: {now}"
    if generated_by:
        meta += f" · oleh {generated_by}"
    story.append(Paragraph(meta, s["meta"]))

    for line_txt in lines or []:
        story.append(Paragraph(line_txt, s["body"]))
        story.append(Spacer(1, 2 * mm))

    if table_headers and table_rows is not None:
        money_cols = [i for i, h in enumerate(table_headers) if _is_money_header(h)]
        money_col_set = set(money_cols)
        data = [table_headers] + [
            [
                format_money(c) if i in money_col_set else (str(c) if c is not None else "")
                for i, c in enumerate(r)
            ]
            for r in table_rows
        ]
        tbl = Table(data, repeatRows=1)
        style_cmds: list[tuple] = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{primary}")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C8C8C8")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for ci in money_cols:
            style_cmds.append(("ALIGN", (ci, 0), (ci, -1), "RIGHT"))
        for r_idx, row in enumerate(table_rows, start=1):
            if _row_is_total(row):
                style_cmds.append(("BACKGROUND", (0, r_idx), (-1, r_idx), colors.HexColor("#DCE8F8")))
                style_cmds.append(("FONTNAME", (0, r_idx), (-1, r_idx), "Helvetica-Bold"))
        tbl.setStyle(TableStyle(style_cmds))
        story.append(tbl)

    story.append(Spacer(1, 14 * mm))
    left_name = branding.signatory_left_name or "(........................)"
    mid_name = branding.signatory_mid_name or "(........................)"
    right_name = branding.signatory_right_name or "(........................)"
    sign_data = [
        [
            Paragraph(branding.signatory_left_title, s["sign_role"]),
            Paragraph(branding.signatory_mid_title, s["sign_role"]),
            Paragraph(branding.signatory_right_title, s["sign_role"]),
        ],
        [
            Paragraph(left_name, s["sign_name"]),
            Paragraph(mid_name, s["sign_name"]),
            Paragraph(right_name, s["sign_name"]),
        ],
    ]
    sign_tbl = Table(sign_data, colWidths=[doc.width / 3.0] * 3)
    sign_tbl.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 1), (-1, 1), 20),
            ]
        )
    )
    story.append(sign_tbl)

    if footer:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(footer, s["footer"]))

    doc.build(story)
    return buf.getvalue()


def generate_pdf_report(
    *,
    title: str,
    subtitle: str = "",
    lines: Optional[list[str]] = None,
    table_headers: Optional[list[str]] = None,
    table_rows: Optional[list[list[Any]]] = None,
    footer: str = "",
    landscape: bool = False,
    branding: Optional[ReportBranding] = None,
    generated_by: str = "",
) -> bytes:
    """Audit-ready PDF: WeasyPrint HTML when available, else Platypus letterhead."""
    if lines:
        return generate_pdf_report_platypus(
            title=title,
            subtitle=subtitle,
            lines=lines,
            table_headers=table_headers,
            table_rows=table_rows,
            footer=footer,
            landscape=landscape,
            branding=branding,
            generated_by=generated_by,
        )
    from modules.siabumdes.adapters.external.html_pdf import generate_audit_pdf

    return generate_audit_pdf(
        title=title,
        subtitle=subtitle,
        table_headers=table_headers,
        table_rows=table_rows,
        footer=footer,
        landscape=landscape,
        branding=branding,
        generated_by=generated_by,
    )


def generate_kwitansi_pdf(
    *,
    no: str,
    tanggal: str,
    diterima_dari: str,
    jumlah: str,
    untuk: str,
    penerima: str = "",
) -> bytes:
    """Receipt (kwitansi) PDF."""
    lines = [
        f"No: <b>{no}</b>",
        f"Tanggal: {tanggal}",
        f"Telah diterima dari: <b>{diterima_dari}</b>",
        f"Uang sejumlah: <b>{jumlah}</b>",
        f"Untuk pembayaran: {untuk}",
    ]
    if penerima:
        lines.append(f"Penerima: {penerima}")
    return generate_pdf_report(title="KWITANSI", lines=lines)


def generate_invoice_pdf(
    *,
    invoice_no: str,
    date: str,
    customer: str,
    items: list[list[Any]],
    total: str,
) -> bytes:
    """Simple invoice PDF. items = [[desc, qty, price, subtotal], ...]."""
    return generate_pdf_report(
        title="INVOICE",
        subtitle=f"No. {invoice_no} | {date} | {customer}",
        table_headers=["Uraian", "Qty", "Harga", "Subtotal"],
        table_rows=items,
        footer=f"Total: {total}",
    )
