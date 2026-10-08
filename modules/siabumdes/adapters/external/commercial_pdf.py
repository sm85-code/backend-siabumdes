"""Landscape commercial documents inspired by the supplied INV/PO examples."""

import io
from pathlib import Path
import reportlab
from decimal import Decimal
from html import escape

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from modules.siabumdes.report_branding import fetch_logo_bytes

# Embed dependency-bundled fonts for consistent rendering on phones/printers.
_FONT_DIR = Path(reportlab.__file__).parent / "fonts"
pdfmetrics.registerFont(TTFont("CommercialSans", str(_FONT_DIR / "Vera.ttf")))
pdfmetrics.registerFont(TTFont("CommercialSansBold", str(_FONT_DIR / "VeraBd.ttf")))

TITLES = {
    "invoice": "INVOICE",
    "po": "PURCHASE ORDER",
    "purchase_statement": "REKAP PEMBELIAN",
    "statement": "REKAP TRANSAKSI MITRA",
}


def rupiah(value):
    n = Decimal(str(value or 0))
    whole, fraction = f"{n:,.2f}".split(".")
    return "Rp " + whole.replace(",", ".") + ("," + fraction if fraction != "00" else "")


def day(value):
    from datetime import date

    return date.fromisoformat(value).strftime("%d/%m/%Y") if value else "—"


def commercial_pdf(data):
    buf = io.BytesIO()
    width = landscape(A4)[0] - 28 * mm
    blue, pale = colors.HexColor("#7F94B6"), colors.HexColor("#E6EBF2")
    styles = {
        "body": ParagraphStyle("body", fontName="CommercialSans", fontSize=9, leading=13),
        "small": ParagraphStyle(
            "small", fontName="CommercialSans", fontSize=8, leading=11, textColor=colors.HexColor("#526070")
        ),
        "org": ParagraphStyle("org", fontName="CommercialSansBold", fontSize=16, leading=20),
        "title": ParagraphStyle("title", fontName="CommercialSansBold", fontSize=19, leading=24, alignment=TA_CENTER),
        "right": ParagraphStyle("right", fontName="CommercialSans", fontSize=8, leading=12, alignment=TA_RIGHT),
        "th": ParagraphStyle(
            "th", fontName="CommercialSansBold", fontSize=8, leading=11, textColor=colors.white, alignment=TA_CENTER
        ),
    }

    def p(text, style="body"):
        return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), styles[style])

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(pale)
        canvas.line(14 * mm, 13 * mm, landscape(A4)[0] - 14 * mm, 13 * mm)
        canvas.setFont("CommercialSans", 7)
        canvas.setFillColor(colors.HexColor("#526070"))
        canvas.drawString(14 * mm, 9 * mm, data.get("number", ""))
        canvas.drawRightString(landscape(A4)[0] - 14 * mm, 9 * mm, f"Halaman {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=19 * mm,
        title=TITLES[data["kind"]],
        author=data["org_name"],
    )
    logo = []
    raw = fetch_logo_bytes(data.get("logo_url", ""))
    if raw:
        try:
            im = Image(io.BytesIO(raw))
            ratio = min(25 * mm / im.imageWidth, 25 * mm / im.imageHeight)
            im.drawWidth, im.drawHeight = im.imageWidth * ratio, im.imageHeight * ratio
            logo = [im]
        except Exception:
            logo = []
    identity = [
        p(data["org_name"], "org"),
        p(data.get("unit_name", ""), "small"),
        Spacer(1, 3 * mm),
        p(data.get("address", "")),
    ]
    header = Table(
        [
            [
                logo,
                identity,
                [
                    p(TITLES[data["kind"]] + (" — DIBATALKAN" if data.get("status") == "void" else ""), "title"),
                    Spacer(1, 3 * mm),
                    p(f"Periode: {day(data.get('period_start'))} s.d. {day(data.get('period_end'))}", "small"),
                ],
            ]
        ],
        colWidths=[30 * mm, width * 0.51 - 30 * mm, width * 0.49],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (2, 0), (2, 0), 0.7, colors.HexColor("#425571")),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (2, 0), (2, 0), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    metadata = [
        p(f"Nomor: {data.get('number', 'PRATINJAU')}"),
        p(f"Tanggal dokumen: {day(data['issued_date'])}"),
        p(f"Jatuh tempo / target pembayaran: {day(data.get('due_date'))}"),
    ]
    partner = [
        p("Kepada Yth.", "small"),
        p(data.get("partner_name", "Seluruh mitra"), "org"),
        p(data.get("partner_address", "")),
        p(data.get("partner_contact", ""), "small"),
    ]
    meta = Table([[metadata, partner]], colWidths=[width * 0.57, width * 0.43])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story = [
        header,
        Spacer(1, 9 * mm),
        meta,
        Spacer(1, 9 * mm),
        p("Deskripsi Barang / Jasa" if data["kind"] != "statement" else "Rincian Transaksi dan Pembayaran"),
        Spacer(1, 3 * mm),
    ]
    invoice = data["kind"] == "invoice"
    statement = data["kind"] == "statement"
    if statement:
        labels = ["Tanggal / Invoice", "Mitra / Barang", "Qty", "Nilai Transaksi", "Dibayar", "Sisa Tagihan"]
        widths = [0.17, 0.31, 0.06, 0.16, 0.15, 0.15]
    elif invoice:
        labels = [
            "Tanggal",
            "Nama Barang / Jasa",
            "Ukuran / Qty",
            "Harga Barang",
            "Packing / Tambahan",
            "Proses Pesanan",
            "Total",
        ]
        widths = [0.10, 0.28, 0.10, 0.13, 0.13, 0.12, 0.14]
    else:
        labels = ["Tanggal", "Kode Pesanan", "Nama Barang", "Ukuran", "Qty", "Harga Satuan", "Total"]
        widths = [0.10, 0.21, 0.23, 0.11, 0.06, 0.13, 0.16]
    rows = [[p(h, "th") for h in labels]]
    for item in data["items"]:
        if statement:
            values = [
                day(item["date"]) + "\n" + (item.get("invoice_number") or "—"),
                item.get("partner_name", "") + "\n" + item["name"],
                item["quantity"],
                rupiah(item["total"]),
                rupiah(item["paid"]),
                rupiah(item["outstanding"]),
            ]
        elif invoice:
            values = [
                day(item["date"]),
                item["name"],
                (item.get("size") or "—") + f"\n{item['quantity']} {item.get('unit', '')}",
                rupiah(item.get("goods", Decimal(item["price"]) * item["quantity"])),
                rupiah(item["packing"]),
                rupiah(item["processing"]),
                rupiah(item["total"]),
            ]
        else:
            values = [
                day(item["date"]),
                item.get("order_code") or "—",
                item["name"],
                item.get("size") or "—",
                item["quantity"],
                rupiah(item["price"]),
                rupiah(item["total"]),
            ]
        money_from = 3 if invoice or statement else 5
        rows.append([p(v, "right" if i >= money_from else "body") for i, v in enumerate(values)])
    total = ["TOTAL"] + [""] * (len(labels) - 2) + [rupiah(data["total"])]
    if statement:
        total = [
            "TOTAL",
            "",
            sum(i["quantity"] for i in data["items"]),
            rupiah(data["total"]),
            rupiah(sum(Decimal(i["paid"]) for i in data["items"])),
            rupiah(sum(Decimal(i["outstanding"]) for i in data["items"])),
        ]
    if invoice:
        total = [
            "TOTAL",
            "",
            "",
            rupiah(sum(Decimal(i.get("goods", Decimal(i["price"]) * i["quantity"])) for i in data["items"])),
            rupiah(sum(Decimal(i["packing"]) for i in data["items"])),
            rupiah(sum(Decimal(i["processing"]) for i in data["items"])),
            rupiah(data["total"]),
        ]
    rows.append([p(v, "right" if i >= 3 else "body") for i, v in enumerate(total)])
    table = Table(rows, colWidths=[width * w for w in widths], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("BACKGROUND", (0, -1), (-1, -1), pale),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F7F9FC")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 1, blue),
                ("LINEABOVE", (0, -1), (-1, -1), 1, blue),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, pale),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(table)
    if "paid" in data:
        story.extend(
            [
                Spacer(1, 4 * mm),
                p(
                    f"Pembayaran diterima/dilakukan: {rupiah(data['paid'])}    Sisa tagihan: {rupiah(data['outstanding'])}",
                    "right",
                ),
            ]
        )
    if data.get("payment_info"):
        story.append(
            KeepTogether([Spacer(1, 8 * mm), p("Informasi Pembayaran"), Spacer(1, 2 * mm), p(data["payment_info"])])
        )
    if statement:
        history = []
        for item in data["items"]:
            for pay in item.get("payments", []):
                history.append(
                    p(
                        f"{day(pay['date'])} · {item.get('partner_name', '')} · {item.get('invoice_number') or item['name']} · {rupiah(pay['amount'])}",
                        "small",
                    )
                )
        if history:
            story.extend([Spacer(1, 6 * mm), p("Riwayat Pembayaran"), *history])
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
