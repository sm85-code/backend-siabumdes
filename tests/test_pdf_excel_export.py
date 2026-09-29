"""Regresi untuk export PDF/Excel laporan keuangan.

Latar belakang (historis): weasyprint==62.3 pernah tidak kompatibel dengan
pydyf 0.12+ (Stream.transform() memanggil API yang sudah dihapus), sehingga
render PDF diam-diam gagal lalu jatuh ke fallback ReportLab (lihat
generate_pdf_report -> html_pdf.render_pdf_from_html yang menelan
exception-nya jadi cuma warning log). Sejak weasyprint 70.0 + pydyf 0.12.1
(lihat requirements.txt) masalah ini sudah tidak ada, tapi test ini tetap
dipertahankan sebagai guard: memastikan jalur WeasyPrint benar-benar jalan
(bukan diam-diam fallback) kalau ada regresi versi serupa di masa depan.
"""
from __future__ import annotations

import os

import pytest

from modules.siabumdes.adapters.external.excel_adapter import generate_excel_report
from modules.siabumdes.adapters.external.html_pdf import render_pdf_from_html, build_report_html
from modules.siabumdes.adapters.external.pdf_generator import generate_pdf_report_platypus
from modules.siabumdes.adapters.external.word_generator import generate_word_report

SAMPLE_HEADERS = ["Kode", "Nama", "Nominal"]
SAMPLE_ROWS = [
    ["4.1.01", "Pendapatan Jasa", 15000000],
    ["Total Pendapatan", "", 15000000],
]


def test_weasyprint_pdf_path_does_not_silently_fall_back():
    """Guards against the pydyf-version regression: render_pdf_from_html
    must return real bytes, not None (which would mean it swallowed an
    exception and the caller fell back to the ReportLab renderer)."""
    html_doc = build_report_html(title="Laba Rugi", table_headers=SAMPLE_HEADERS, table_rows=SAMPLE_ROWS)
    blob = render_pdf_from_html(html_doc)
    assert blob is not None, "WeasyPrint gagal render -- cek kecocokan versi pydyf di requirements.txt"
    assert blob.startswith(b"%PDF")


def test_reportlab_fallback_pdf_still_works_standalone():
    blob = generate_pdf_report_platypus(title="Laba Rugi", table_headers=SAMPLE_HEADERS, table_rows=SAMPLE_ROWS)
    assert blob.startswith(b"%PDF")


def test_generate_excel_report_has_letterhead_headers_and_currency_format():
    blob = generate_excel_report(SAMPLE_HEADERS, SAMPLE_ROWS, "Laba Rugi")
    from openpyxl import load_workbook
    import io

    wb = load_workbook(io.BytesIO(blob))
    ws = wb.active
    header_row_values = [c.value for c in ws[8]]
    assert header_row_values == SAMPLE_HEADERS
    nominal_cell = ws.cell(row=9, column=3)
    assert nominal_cell.value == 15000000
    assert "Rp" in nominal_cell.number_format


def test_generate_word_report_is_a_valid_docx_with_letterhead_and_table():
    blob = generate_word_report(title="Laba Rugi", subtitle="Jan 2024", table_headers=SAMPLE_HEADERS, table_rows=SAMPLE_ROWS)
    assert blob.startswith(b"PK")  # docx is a zip archive

    from docx import Document
    import io

    doc = Document(io.BytesIO(blob))
    body_text = "\n".join(p.text for p in doc.paragraphs)
    assert "LABA RUGI" in body_text
    assert "Jan 2024" in body_text
    assert len(doc.tables) == 2  # data table + signature table
    header_cells = [c.text for c in doc.tables[0].rows[0].cells]
    assert header_cells == SAMPLE_HEADERS


@pytest.mark.asyncio
async def test_report_router_pdf_excel_word_helpers_run_off_the_event_loop(monkeypatch):
    """modules.siabumdes.adapters.api.v1.reports_router._pdf/_xlsx/_docx wrap the blocking
    generate_*_report calls in starlette's run_in_threadpool. This smoke
    test exercises them the same way the report_pdf/report_excel/report_word
    route handlers do, confirming they still produce valid output once
    awaited from a thread pool instead of being called directly."""
    # Importing reports_router pulls in shared.database, which raises at
    # import time without DATABASE_URL (unset in CI). The helpers under test
    # never touch the DB, and create_async_engine doesn't connect, so a
    # placeholder URL is enough to get past the import.
    if not (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")):
        monkeypatch.setenv("DATABASE_URL", "postgresql://placeholder@localhost/placeholder")
    from modules.siabumdes.adapters.api.v1.reports_router import _docx, _pdf, _xlsx

    pdf_response = await _pdf("Laba Rugi", SAMPLE_HEADERS, SAMPLE_ROWS, "Jan 2024")
    assert pdf_response.media_type == "application/pdf"
    assert bytes(pdf_response.body).startswith(b"%PDF")

    xlsx_response = await _xlsx("Laba Rugi", SAMPLE_HEADERS, SAMPLE_ROWS, "Jan 2024")
    assert xlsx_response.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert bytes(xlsx_response.body).startswith(b"PK")

    docx_response = await _docx("Laba Rugi", SAMPLE_HEADERS, SAMPLE_ROWS, "Jan 2024")
    assert docx_response.media_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert bytes(docx_response.body).startswith(b"PK")
