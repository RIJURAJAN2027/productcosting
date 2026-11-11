"""Excel (openpyxl) and PDF (reportlab) exports in the familiar report layout.

Excel sheets follow the legacy layout: labels in column B, header rows
Plant / Product Group / Product family / "Rs - %" in rows 2-5, values from row 6,
with the row styles (bold, fill) of the company's report template.
"""
from __future__ import annotations

import datetime as dt
import io
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NUM_FMT = {"indian": '[>=10000000]##\\,##\\,##\\,##0.00;[>=100000]##\\,##\\,##0.00;##,##0.00',
           "international": "#,##0.00"}
NEG_RED = ';[Red]-'
PCT_FMT = "0.0%;[Red]-0.0%"
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
SUB_FILL = PatternFill("solid", fgColor="DCE6F1")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(bottom=THIN)


def _num_format(number_format: str, decimals: int) -> str:
    base = "#,##0" + ("." + "0" * decimals if decimals else "")
    if number_format == "indian":
        return (f'[>=10000000]##\\,##\\,##\\,##0{"." + "0" * decimals if decimals else ""};'
                f'[>=100000]##\\,##\\,##0{"." + "0" * decimals if decimals else ""};{base}')
    return f"{base};[Red]-{base}"


def _fill(color: str | None) -> PatternFill | None:
    if not color or not isinstance(color, str) or len(color) < 6 or color in ("00000000",):
        return None
    return PatternFill("solid", fgColor=color[-6:])


def write_report_sheet(ws, report: dict, number_format: str = "indian", decimals: int = 2) -> None:
    """Write one P&L report (output of reports.build_report) to a worksheet."""
    nf = _num_format(number_format, decimals)
    cols = report["columns"]
    ws["A1"] = report.get("company", "")
    ws["A1"].font = Font(bold=True, size=13)
    ws["C1"] = f"{report['title']} — {report['period']['label'] or report['period']['code']}"
    ws["C1"].font = Font(bold=True, size=12)
    ws.cell(1, 3 + 2 * len(cols) - 2, f"Amounts in {report['unit']}").font = Font(italic=True)
    for r, label in ((2, "Plant"), (3, "Product Group"), (4, "Product family")):
        ws.cell(r, 2, label).font = Font(bold=True)
    for i, c in enumerate(cols):
        col = 3 + 2 * i
        ws.cell(2, col, "Company" if c["plant"] == "__ALL__" else c["plant"])
        ws.cell(3, col, "Total" if c["code"] == "__TOTAL__" else c["code"])
        ws.cell(4, col, c.get("family") or "")
        ws.cell(5, col, "Rs")
        ws.cell(5, col + 1, "%")
        ws.merge_cells(start_row=2, start_column=col, end_row=2, end_column=col + 1)
        ws.merge_cells(start_row=3, start_column=col, end_row=3, end_column=col + 1)
        ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col + 1)
        for rr in (2, 3, 4, 5):
            for cc in (col, col + 1):
                cell = ws.cell(rr, cc)
                cell.fill = HEADER_FILL if rr < 5 else SUB_FILL
                cell.font = HEADER_FONT if rr < 5 else Font(bold=True)
                cell.alignment = Alignment(horizontal="center")
    r = 6
    for row in report["rows"]:
        style = row.get("style") or {}
        label_cell = ws.cell(r, 2, row.get("label", ""))
        bold = bool(style.get("bold")) or row.get("kind") == "header"
        label_cell.font = Font(bold=bold, italic=bool(style.get("italic")))
        if style.get("indent"):
            label_cell.alignment = Alignment(indent=int(style["indent"]))
        fill = _fill(style.get("fill"))
        if row.get("kind") == "line":
            for i, v in enumerate(row["values"]):
                col = 3 + 2 * i
                c = ws.cell(r, col, round(v, 10) if v is not None else None)
                c.number_format = nf
                c.font = Font(bold=bold)
                p = row["pct"][i] if row.get("pct") else None
                if p is not None:
                    pc = ws.cell(r, col + 1, p)
                    pc.number_format = PCT_FMT
                    pc.font = Font(italic=True, color="555555")
                if fill:
                    ws.cell(r, col).fill = fill
                    ws.cell(r, col + 1).fill = fill
        if fill:
            label_cell.fill = fill
        r += 1
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 44
    for i in range(len(cols)):
        ws.column_dimensions[get_column_letter(3 + 2 * i)].width = 13
        ws.column_dimensions[get_column_letter(4 + 2 * i)].width = 8
    ws.freeze_panes = "C6"
    ws.sheet_view.zoomScale = 85


def write_table(ws, title: str, headers: list[str], rows: Iterable[list], formats: list[str | None] | None = None,
                subtitle: str | None = None) -> None:
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=13)
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = Font(italic=True)
    for j, h in enumerate(headers, start=1):
        c = ws.cell(4, j, h)
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(j)].width = max(12, min(48, len(h) + 4))
    i = 5
    for row in rows:
        for j, v in enumerate(row, start=1):
            c = ws.cell(i, j, v)
            if formats and j - 1 < len(formats) and formats[j - 1] and isinstance(v, (int, float)):
                c.number_format = formats[j - 1]
        i += 1
    ws.freeze_panes = "A5"


def report_workbook(reports: list[tuple[str, dict]], number_format: str = "indian", decimals: int = 2,
                    extra: list[tuple[str, str, list[str], list[list], list[str | None]]] | None = None) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for sheet_name, rep in reports:
        ws = wb.create_sheet(sheet_name[:31])
        write_report_sheet(ws, rep, number_format, decimals)
    for sheet_name, title, headers, rows, formats in extra or []:
        write_table(wb.create_sheet(sheet_name[:31]), title, headers, rows, formats)
    wb.properties.creator = "Product Costing & Profitability"
    wb.properties.created = dt.datetime.now()
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def mc_rows(rep: dict) -> tuple[list[str], list[list], list[str | None]]:
    headers = ["Plant", "Product group", "Sales", "Material cost (TB)", "MC adjustment", "Material cost (worked out)",
               "MC % (TB)", "MC % (worked out)"]
    nf = "#,##0.00;[Red]-#,##0.00"
    rows = [[r["plant"].replace("__ALL__", "Company"), r["pg"].replace("__TOTAL__", "Total"), r["sales"], r["mc_tb"],
             r["mc_adjustment"], r["mc_worked_out"], r["mc_pct_tb"], r["mc_pct"]] for r in rep["rows"]]
    return headers, rows, [None, None, nf, nf, nf, nf, PCT_FMT, PCT_FMT]


def mis_rows(rep: dict) -> tuple[list[str], list[list], list[str | None]]:
    headers = ["Particulars", "MIS", "Product costing", "Difference", "Status"]
    nf = "#,##0.000;[Red]-#,##0.000"
    rows = [[r["label"], r.get("mis"), r.get("pnl"), r.get("difference"),
             "OK" if r.get("ok") else ("ERROR" if r.get("error") else "DIFFERENCE")] for r in rep["rows"]]
    return headers, rows, [None, nf, nf, nf, None]


def controls_rows(controls: list[dict], unit_div: float) -> tuple[list[str], list[list], list[str | None]]:
    headers = ["Control", "Status", "Amount", "Details"]
    rows = []
    for c in controls:
        det = "; ".join(f"{d.get('key') or d.get('item')}: {d.get('amount', 0):,.4f}" for d in c.get("details", [])[:12])
        rows.append([c["title"], c["level"].upper(), c["amount"] * unit_div, det])
    return headers, rows, [None, None, "#,##0.0000;[Red]-#,##0.0000", None]


# ----------------------------------------------------------------------- PDF
def report_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A3), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=report["title"], author="Product Costing & Profitability")
    styles = getSampleStyleSheet()
    story = [Paragraph(f"<b>{report.get('company', '')}</b> — {report['title']}", styles["Title"]),
             Paragraph(f"Period: {report['period']['label'] or report['period']['code']} · Run #{report['run']['id']} "
                       f"({report['run']['status']}) · Amounts in {report['unit']}", styles["Normal"]),
             Spacer(1, 4 * mm)]
    cols = report["columns"]
    header = ["Particulars"] + [("Total" if c["code"] == "__TOTAL__" else c["code"]) if report["report"] in
                                ("pg_wise", "all_pg") else ("Company" if c["plant"] == "__ALL__" else c["plant"])
                                for c in cols]
    data = [header]
    bold_rows = []
    for row in report["rows"]:
        if row.get("kind") != "line":
            if row.get("label"):
                data.append([row["label"]] + [""] * len(cols))
                bold_rows.append(len(data) - 1)
            continue
        data.append([row["label"][:48]] + [f"{v:,.2f}" if v else "–" for v in row["values"]])
        if (row.get("style") or {}).get("bold"):
            bold_rows.append(len(data) - 1)
    widths = [70 * mm] + [max(16 * mm, (doc.width - 70 * mm) / max(1, len(cols)))] * len(cols)
    t = LongTable(data, colWidths=widths, repeatRows=1)
    st = [("FONT", (0, 0), (-1, -1), "Helvetica", 6.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
          ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7),
          ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
          ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F8FB")])]
    for r in bold_rows:
        st.append(("FONT", (0, r), (-1, r), "Helvetica-Bold", 6.5))
    t.setStyle(TableStyle(st))
    story.append(t)

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(10 * mm, 6 * mm, f"Generated {dt.datetime.now():%d %b %Y %H:%M} · Product Costing & "
                                           "Profitability")
        canvas.drawRightString(doc_.pagesize[0] - 10 * mm, 6 * mm, f"Page {doc_.page}")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
