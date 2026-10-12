"""Audit-ready PDF from HTML+CSS (WeasyPrint) with ReportLab fallback."""
from __future__ import annotations

import html
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional, Sequence

from modules.siabumdes.adapters.external.report_formatting import column_weights, format_money, is_money_header, looks_numeric, row_is_total
from modules.siabumdes.report_branding import ReportBranding, get_report_branding, theme_tint

logger = logging.getLogger(__name__)

CSS = """
@page {
  size: %(page_size)s;
  margin: 14mm 12mm 18mm 12mm;
  @bottom-center {
    content: "Hal. " counter(page) " / " counter(pages);
    font-size: 8pt;
    color: #666;
  }
}
body {
  font-family: "DejaVu Sans", "Helvetica", "Arial", sans-serif;
  font-size: 9.5pt;
  color: #1a1a1a;
  line-height: 1.35;
}
.letterhead { text-align: left; border-bottom: 2.5px solid #%(primary)s; padding-bottom: 8px; margin-bottom: 14px; text-align: left; }
.letterhead .identity { display: table-cell; width: 58%%; padding-right: 14px; vertical-align: top; }
.letterhead .document { display: table-cell; width: 42%%; border: 1px solid #%(primary)s; padding: 8px; vertical-align: top; }
.letterhead .brand-block { width: 100%%; border-collapse: collapse; }
.letterhead .brand-block td { vertical-align: top; }
.letterhead .logo-cell { width: 18mm; padding-right: 8px; }
.letterhead .logo { max-height: 52px; max-width: 54px; margin-bottom: 4px; }
.letterhead .org { font-size: 14pt; font-weight: 700; color: #%(primary)s; letter-spacing: 0.2px; }
.letterhead .legal { font-size: 8.5pt; color: #444; }
.letterhead .addr { font-size: 8pt; color: #555; white-space: pre-line; margin-top: 2px; }
.doc-title { text-align: center; font-size: 12.5pt; font-weight: 700; margin: 10px 0 2px; text-transform: uppercase; }
.doc-sub { text-align: center; font-size: 9pt; color: #444; margin-bottom: 4px; }
.meta { text-align: center; font-size: 8pt; color: #666; margin-bottom: 12px; }
table.report { width: 100%%; border-collapse: collapse; margin-top: 6px; table-layout: fixed; }
table.report thead { display: table-header-group; }
table.report tr { break-inside: avoid; }
table.report .prose { white-space: normal; line-height: 1.55; text-align: justify; text-align-last: left; }
table.report tbody tr:nth-child(even) { background: #f7f9fc; }
.signatures { break-inside: avoid; }
table.report th {
  background: #%(primary)s; color: #fff; font-weight: 700; font-size: 8.5pt;
  padding: 9px 8px; border: 0.4pt solid #%(primary)s; text-align: left;
  overflow-wrap: break-word;
}
table.report td {
  padding: 6px 8px; border-bottom: 0.35pt solid #c8c8c8; vertical-align: top; font-size: 8.5pt;
  overflow-wrap: break-word; word-break: break-word;
}
table.report tr.section td { background: #%(pale)s; font-weight: 700; }
table.report tr.total td { background: #%(pale)s; font-weight: 700; border-top: 1.2pt solid #%(primary)s; }
table.report td.num, table.report th.num {
  text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap;
}
.callouts { display: flex; gap: 10px; margin: 8px 0 12px; }
.callout {
  flex: 1; border: 0.6pt solid #c5d0de; border-radius: 4px; padding: 6px 8px; background: #f7f9fc;
}
.callout .lbl { font-size: 7.5pt; color: #666; text-transform: uppercase; }
.callout .val { font-size: 10pt; font-weight: 700; color: #%(primary)s; }
.signatures { margin-top: 20px; width: 100%%; border-collapse: collapse; }
.signatures td { width: 33%%; text-align: center; vertical-align: top; padding: 0 8px; font-size: 8.5pt; }
.signatures .role { font-weight: 600; margin-bottom: 28px; }
.signatures .line { border-top: 0.6pt solid #333; margin: 0 12px; padding-top: 4px; min-height: 18px; }
.footer-note { margin-top: 14px; font-size: 7.5pt; color: #777; text-align: center; }
"""


def _esc(v: Any) -> str:
    if v is None:
        return ""
    return html.escape(str(v))


_is_money_header = is_money_header


def _row_class(row: Sequence[Any]) -> str:
    if row_is_total(list(row)):
        return "total"
    cells = [str(c or "") for c in row]
    # A free-text sentence (e.g. a CaLK policy note) is never a section
    # header, even if it happens to mention "aset"/"kewajiban"/"pendapatan" --
    # only a short label-only row like ["", "ASET"] should get that styling.
    # A period, or more than a handful of words, means it's prose, not a label.
    if any("." in c or len(c.split()) > 5 for c in cells):
        return ""
    joined = " ".join(c.lower() for c in cells)
    if (
        any(
            k in joined
            for k in (
                "aset", "kewajiban", "ekuitas", "pendapatan", "beban",
                "operasi", "investasi", "pendanaan",
            )
        )
        and len([c for c in row if c not in (None, "")]) <= 2
    ):
        return "section"
    return ""


def build_report_html(
    *,
    title: str,
    subtitle: str = "",
    table_headers: Optional[list[str]] = None,
    table_rows: Optional[list[list[Any]]] = None,
    callouts: Optional[list[tuple[str, str]]] = None,
    footer: str = "",
    landscape: bool = False,
    branding: Optional[ReportBranding] = None,
    generated_by: str = "",
) -> str:
    b = branding or get_report_branding()
    page_size = "A4 landscape" if landscape else "A4"
    css = CSS % {"primary": b.primary_color, "page_size": page_size, "pale": theme_tint(b.primary_color)}
    headers = table_headers or []
    rows = table_rows or []
    money_cols = {i for i, h in enumerate(headers) if _is_money_header(h)}

    thead = "".join(
        f'<th class="{"num" if i in money_cols else ""}">{_esc(h)}</th>'
        for i, h in enumerate(headers)
    )
    columns = "".join(f'<col style="width:{weight * 100:.2f}%"/>' for weight in column_weights(headers))
    body_rows = []
    for row in rows:
        cls = _row_class(row)
        prose = len(headers) == 2 and len(row) == 2 and not row[1] and len(str(row[0])) > 60
        tds = f'<td colspan="2" class="prose">{_esc(row[0])}</td>' if prose else "".join(
            f'<td class="{"num" if i in money_cols and looks_numeric(cell) else ""}">'
            f'{_esc(format_money(cell) if i in money_cols and looks_numeric(cell) else cell)}</td>'
            for i, cell in enumerate(row)
        )
        body_rows.append(f'<tr class="{cls}">{tds}</tr>')

    callout_html = ""
    if callouts:
        boxes = [
            (
                f'<div class="callout"><div class="lbl">{_esc(lbl)}</div>'
                f'<div class="val">{_esc(val)}</div></div>'
            )
            for lbl, val in callouts
        ]
        callout_html = f'<div class="callouts">{"".join(boxes)}</div>'

    now = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%d/%m/%Y %H:%M WIB")
    gen = f"Dicetak: {now}"
    if generated_by:
        gen += f" · oleh {html.escape(generated_by)}"

    left_name = b.signatory_left_name or "(........................)"
    mid_name = b.signatory_mid_name or "(........................)"
    right_name = b.signatory_right_name or "(........................)"

    sub_html = f'<div class="doc-sub">{_esc(subtitle)}</div>' if subtitle else ""
    foot_html = f'<div class="footer-note">{_esc(footer)}</div>' if footer else ""

    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8"/><style>{css}</style></head>
<body>
  <div class="letterhead">
    <div class="identity">
    <table class="brand-block"><tr>
    {f'<td class="logo-cell"><img class="logo" src="{_esc(b.logo_url)}"/></td>' if b.logo_url else ""}<td>
    <div class="org">{_esc(b.org_name)}</div>
    <div class="legal">{_esc(b.org_legal_name)}</div>
    <div class="addr">{_esc(b.address_block)}</div>
    </td></tr></table></div><div class="document"><div class="doc-title">{_esc(title)}</div>{sub_html}</div>
  </div>
  <div class="meta">{gen}</div>
  {callout_html}
  <table class="report"><colgroup>{columns}</colgroup>
    <thead><tr>{thead}</tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
  <table class="signatures"><tr>
    <td><div class="role">{_esc(b.signatory_left_title)}</div><div class="line">{_esc(left_name)}</div></td>
    <td><div class="role">{_esc(b.signatory_mid_title)}</div><div class="line">{_esc(mid_name)}</div></td>
    <td><div class="role">{_esc(b.signatory_right_title)}</div><div class="line">{_esc(right_name)}</div></td>
  </tr></table>
  {foot_html}
</body></html>"""


def render_pdf_from_html(html_doc: str) -> Optional[bytes]:
    try:
        from weasyprint import HTML  # type: ignore

        return HTML(string=html_doc, base_url=".").write_pdf()
    except Exception as exc:  # noqa: BLE001
        logger.warning("WeasyPrint unavailable (%s); using ReportLab fallback", exc)
        return None


def generate_audit_pdf(
    *,
    title: str,
    subtitle: str = "",
    table_headers: Optional[list[str]] = None,
    table_rows: Optional[list[list[Any]]] = None,
    callouts: Optional[list[tuple[str, str]]] = None,
    footer: str = "",
    landscape: bool = False,
    branding: Optional[ReportBranding] = None,
    generated_by: str = "",
) -> bytes:
    """Prefer WeasyPrint HTML; fall back to Platypus letterhead PDF."""
    doc = build_report_html(
        title=title,
        subtitle=subtitle,
        table_headers=table_headers,
        table_rows=table_rows,
        callouts=callouts,
        footer=footer,
        landscape=landscape,
        branding=branding,
        generated_by=generated_by,
    )
    blob = render_pdf_from_html(doc)
    if blob is not None:
        return blob
    from modules.siabumdes.adapters.external.pdf_generator import generate_pdf_report_platypus

    return generate_pdf_report_platypus(
        title=title,
        subtitle=subtitle,
        table_headers=table_headers,
        table_rows=table_rows,
        footer=footer,
        landscape=landscape,
        branding=branding,
        generated_by=generated_by,
    )
