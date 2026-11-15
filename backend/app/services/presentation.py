"""Management presentation in the layout of the workbook's "PPT formats" sheet, as PowerPoint and PDF.

Slides: plant-wise performance; PG-wise performance for each plant (top product groups, Others, total);
one product group compared across plants; the company as a whole.  Every figure comes from the stored
calculation run, so the presentation always agrees with the reports.  The PowerPoint is marked as final
(it opens read-only); the PDF has the same slides.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import CalcRun
from ..settings import get_settings
from .config_builder import company_settings, get_template
from .reports import ALL, TOTAL, key_line_codes, run_context, unit_divisor
from .runs import value_lookup

NAVY = (0x0B, 0x3D, 0x6E)
NAVY_SOFT = (0xE8, 0xEF, 0xF7)
GREY = (0x6B, 0x72, 0x80)
LINE = (0xD8, 0xDE, 0xE8)
RED = (0xC2, 0x41, 0x0C)
def _brand(name: str) -> Path:
    """Logo file: from the served UI build (Docker / server) or the source tree (development)."""
    here = Path(__file__).resolve()
    roots = [get_settings().frontend_dist, here.parents[2] / "static", here.parents[3] / "frontend" / "dist",
             here.parents[3] / "frontend" / "public"]
    for r in roots:
        if r and (Path(r) / "brand" / name).exists():
            return Path(r) / "brand" / name
    return Path("/nonexistent") / name

# rows of each performance table: (label, key or tuple of keys added together, bold)
ROWS: list[tuple[str, tuple[str, ...], bool]] = [
    ("Sales", ("sales",), True),
    ("Material cost", ("material_cost",), False),
    ("Variable cost", ("variable_cost",), False),
    ("Gross margin", ("gross_margin",), True),
    ("Factory overhead", ("factory_overhead",), False),
    ("Operations employee cost", ("pre_factory",), False),
    ("Plant profit", ("plant_profit",), True),
    ("SGA expenses", ("corporate_overheads", "pre_corporate"), False),
    ("Other credits / (debits)", ("other_credits",), False),
    ("PBT", ("pbt",), True),
]
LABELS = {
    "sales": ["sales"], "material_cost": ["material cost"], "variable_cost": ["variable cost"],
    "gross_margin": ["gross margin"], "factory_overhead": ["factory overhead"], "pre_factory": ["pre-factory related"],
    "plant_profit": ["plant profit"], "corporate_overheads": ["corporate overheads"], "pre_corporate": ["pre-corporate"],
    "other_credits": ["other incomes/ (expenses)", "other credits/ (debits)"], "pbt": ["pbt"],
}
SPLIT = [("Factory overhead", "Factory overhead"), ("PRE-factory related", "Operations employee cost")]


@dataclass
class Table:
    columns: list[str]                       # column groups (each has Rs and %)
    rows: list[tuple[str, list[float | None], list[float | None], bool]]   # label, values, pct, bold
    pct: bool = True
    first_header: str = "Description"


@dataclass
class Slide:
    title: str
    subtitle: str = ""
    tables: list[Table] = field(default_factory=list)
    note: str = ""


@dataclass
class Deck:
    company: str
    period: str
    run: str
    unit: str
    slides: list[Slide]


def _find_line(cfg, label: str) -> str | None:
    want = " ".join(label.lower().split())
    for ln in cfg.lines:
        if " ".join(str(ln.get("label", "")).lower().split()) == want:
            return ln["code"]
    return None


def build_deck(db: Session, run: CalcRun, unit: str | None = None, top: int = 5) -> Deck:
    company, period, cfg = run_context(db, run)
    div, unit_key = unit_divisor(company, unit)
    unit_name = {"rupees": "Rs", "thousand": "Rs '000", "lakh": "Rs lakh", "million": "Rs million",
                 "crore": "Rs crore"}.get(unit_key, unit_key)
    vals = value_lookup(db, run.id)

    def resolve(layout: str) -> dict[str, str]:
        """Line of each row in a report layout (by its label, as the PPT sheet links to that layout)."""
        template = get_template(db, company, layout, cfg)
        keys = key_line_codes(cfg, template)
        by_label: dict[str, str] = {}
        for r in template:
            if r.get("line") and r.get("label"):
                by_label.setdefault(" ".join(str(r["label"]).lower().split()), r["line"])
        for key, labels in LABELS.items():
            for lab in labels:
                if lab in by_label:
                    keys[key] = by_label[lab]
                    break
        return keys

    plant_keys, pg_keys = resolve("plant_wise"), resolve("pg_wise")
    keys = pg_keys

    def kv(key: str, plant: str, code: str) -> float | None:
        line = keys.get(key)
        if not line:
            return None
        return vals.get((line, plant, code), 0.0) / div

    def figures(cells: list[tuple[str, str] | list[tuple[str, str]]]) -> list[tuple[str, list, list, bool]]:
        """Performance rows for columns; a column may add several cells (e.g. 'Others')."""
        cols = [c if isinstance(c, list) else [c] for c in cells]
        out = []
        sales = [sum((kv("sales", p, g) or 0.0) for p, g in col) for col in cols]
        material = max(abs(company_sales) * 0.001, 1e-9)
        for label, ks, bold in ROWS:
            if not all(k in keys for k in ks):
                continue
            v = [sum((kv(k, p, g) or 0.0) for k in ks for p, g in col) for col in cols]
            pc = [None if label == "Sales" or abs(s) < material else x / s for x, s in zip(v, sales)]
            out.append((label, v, pc, bold))
        return out

    def split_rows(cells: list[tuple[str, str] | list[tuple[str, str]]], level: str) -> list[tuple[str, list, list, bool]]:
        cols = [c if isinstance(c, list) else [c] for c in cells]
        rows = []
        for base, shown in SPLIT:
            for kind, word in (("Direct", "Direct"), ("Allocation", "Allocated")):
                line = _find_line(cfg, f"{base} - {kind} - {level}")
                if not line:
                    continue
                v = [sum(vals.get((line, p, g), 0.0) for p, g in col) / div for col in cols]
                rows.append((f"{shown} – {word.lower()}", v, [None] * len(v), False))
        return rows

    company_sales = kv("sales", ALL, TOTAL) or 0.0
    plants = [p for p in cfg.plants if abs(kv("sales", p, TOTAL) or 0.0) >= max(abs(company_sales) * 0.001, 1e-6)]
    slides: list[Slide] = []

    # 1 plant-wise performance: every reporting plant, from the plant-wise layout
    keys = plant_keys
    cells = [(p, TOTAL) for p in cfg.plants] + [(ALL, TOTAL)]
    t = Table([*cfg.plants, "Total"], figures(cells))
    tables = [t]
    sr = split_rows(cells, "Plant")
    if sr:
        tables.append(Table([*cfg.plants, "Total"], sr, pct=False, first_header="Direct and allocated"))
    slides.append(Slide("Plant-wise performance", f"{period.label} · {unit_name}", tables))
    keys = pg_keys

    # 2 PG-wise performance per plant
    for p in plants:
        pgs = [g for g in cfg.product_groups if abs(kv("sales", p, g) or 0.0) > 1e-6]
        pgs.sort(key=lambda g: -(kv("sales", p, g) or 0.0))
        main, rest = pgs[:top], pgs[top:]
        cols: list = [(p, g) for g in main]
        names = list(main)
        if rest:
            cols.append([(p, g) for g in rest])
            names.append("Others")
        cols.append((p, TOTAL))
        names.append("Total")
        tables = [Table(names, figures(cols))]
        sr = split_rows(cols[:-1], "PG")
        if sr:
            tot = split_rows([(p, TOTAL)], "Plant")
            if tot and len(tot) == len(sr):
                sr = [(a[0], a[1] + b[1], a[2] + b[2], a[3]) for a, b in zip(sr, tot)]
            else:
                sr = [(r[0], r[1] + [sum(r[1])], r[2] + [None], r[3]) for r in sr]
            tables.append(Table(names, sr, pct=False, first_header="Direct and allocated"))
        note = f"Others: {', '.join(rest)}" if rest else ""
        slides.append(Slide(f"PG-wise performance – {p}", f"{period.label} · {unit_name}", tables, note))

    # 3 a product group across plants
    for g in cfg.product_groups:
        where = [p for p in plants if abs(kv("sales", p, g) or 0.0) > 1e-6]
        if len(where) < 2:
            continue
        slides.append(Slide(f"{g} – {' vs '.join(where)}", f"{period.label} · {unit_name}",
                            [Table(where, figures([(p, g) for p in where]))]))

    # 4 company as a whole (split when there are many product groups)
    pgs = [g for g in cfg.product_groups if abs(kv("sales", ALL, g) or 0.0) > 1e-6]
    pgs.sort(key=lambda g: -(kv("sales", ALL, g) or 0.0))
    chunks = [pgs[i:i + 7] for i in range(0, len(pgs), 7)] or [[]]
    for i, chunk in enumerate(chunks):
        names = list(chunk) + (["Total"] if i == len(chunks) - 1 else [])
        cells = [(ALL, g) for g in chunk] + ([(ALL, TOTAL)] if i == len(chunks) - 1 else [])
        title = "Company as a whole" + (f" ({i + 1} of {len(chunks)})" if len(chunks) > 1 else "")
        slides.append(Slide(title, f"{period.label} · {unit_name}", [Table(names, figures(cells))]))

    return Deck(company=company.name, period=period.label or period.code, run=f"Run #{run.id} · {run.status}",
                unit=unit_name, slides=slides)


# --------------------------------------------------------------------------------------------- format
def _fmt(v: float | None, style: str = "indian", d: int = 2) -> str:
    if v is None:
        return ""
    if abs(v) < 0.5 * 10 ** -d:
        return "–"
    s = f"{abs(v):,.{d}f}"
    if style == "indian":
        ip, _, fp = s.replace(",", "").partition(".")
        head, tail = ip[:-3], ip[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail]) if groups else tail
        s = f"{s}.{fp}" if fp else s
    return f"({s})" if v < 0 else s


def _pct(v: float | None) -> str:
    if v is None or abs(v) > 99:
        return ""
    return f"{v * 100:.1f}%"


# --------------------------------------------------------------------------------------------- PowerPoint
def deck_pptx(deck: Deck, number_format: str = "indian") -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    rgb = lambda c: RGBColor(*c)  # noqa: E731
    long_logo, short_logo = _brand("logo-long.png"), _brand("logo-short.png")
    stamp = dt.datetime.now().strftime("%d %b %Y")

    def text(slide, x, y, w, h, s, size=12, bold=False, color=(0x1B, 0x1B, 0x2A), align=None):
        tb = slide.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = s
        p.font.size, p.font.bold, p.font.color.rgb, p.font.name = Pt(size), bold, rgb(color), "Calibri"
        if align:
            p.alignment = align
        return tb

    # cover
    s = prs.slides.add_slide(blank)
    bar = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.18))
    bar.fill.solid(); bar.fill.fore_color.rgb = rgb(NAVY); bar.line.fill.background()
    if long_logo.exists():
        s.shapes.add_picture(str(long_logo), Inches(0.8), Inches(0.9), height=Inches(0.75))
    text(s, Inches(0.8), Inches(2.6), Inches(11), Inches(1), "Product costing – plant-wise performance", 34, True, NAVY)
    text(s, Inches(0.8), Inches(3.5), Inches(11), Inches(0.6), f"{deck.company} · {deck.period}", 18, False, GREY)
    text(s, Inches(0.8), Inches(6.6), Inches(11), Inches(0.4), f"{deck.run} · amounts in {deck.unit} · {stamp}", 11, False, GREY)

    for n, sl in enumerate(deck.slides, start=2):
        s = prs.slides.add_slide(blank)
        bar = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.1))
        bar.fill.solid(); bar.fill.fore_color.rgb = rgb(NAVY); bar.line.fill.background()
        text(s, Inches(0.5), Inches(0.3), Inches(10), Inches(0.6), sl.title, 24, True, NAVY)
        text(s, Inches(0.5), Inches(0.85), Inches(10), Inches(0.4), sl.subtitle, 12, False, GREY)
        if short_logo.exists():
            s.shapes.add_picture(str(short_logo), Inches(12.3), Inches(0.3), height=Inches(0.55))
        y = Inches(1.4)
        avail = prs.slide_height - y - Inches(0.6)
        total_rows = sum(len(t.rows) + 2 for t in sl.tables) + (len(sl.tables) - 1)
        rh = min(Inches(0.34), int(avail / max(total_rows, 1)))
        for t in sl.tables:
            ncols = 1 + len(t.columns) * (2 if t.pct else 1)
            nrows = len(t.rows) + (2 if t.pct else 1)
            width = prs.slide_width - Inches(1.0)
            shape = s.shapes.add_table(nrows, ncols, Inches(0.5), y, width, rh * nrows)
            tbl = shape.table
            first = Inches(2.6)
            other = int((width - first) / (ncols - 1))
            tbl.columns[0].width = first
            for j in range(1, ncols):
                tbl.columns[j].width = other
            for i in range(nrows):
                tbl.rows[i].height = rh

            def cell(i, j, s_, bold=False, fill=None, color=(0x1B, 0x1B, 0x2A), align=PP_ALIGN.RIGHT, size=11):
                c = tbl.cell(i, j)
                c.text = s_
                c.margin_left = c.margin_right = Emu(60000)
                c.margin_top = c.margin_bottom = Emu(20000)
                para = c.text_frame.paragraphs[0]
                para.alignment = align
                para.font.size, para.font.bold, para.font.color.rgb, para.font.name = Pt(size), bold, rgb(color), "Calibri"
                c.fill.solid()
                c.fill.fore_color.rgb = rgb(fill or (0xFF, 0xFF, 0xFF))

            cell(0, 0, t.first_header, True, NAVY, (0xFF, 0xFF, 0xFF), PP_ALIGN.LEFT)
            for k, name in enumerate(t.columns):
                j = 1 + k * (2 if t.pct else 1)
                cell(0, j, name, True, NAVY, (0xFF, 0xFF, 0xFF), PP_ALIGN.CENTER)
                if t.pct:
                    tbl.cell(0, j).merge(tbl.cell(0, j + 1))
            r0 = 1
            if t.pct:
                cell(1, 0, "", False, NAVY_SOFT)
                for k in range(len(t.columns)):
                    cell(1, 1 + 2 * k, "Rs", True, NAVY_SOFT, NAVY, PP_ALIGN.RIGHT, 10)
                    cell(1, 2 + 2 * k, "%", True, NAVY_SOFT, NAVY, PP_ALIGN.RIGHT, 10)
                r0 = 2
            for i, (label, v, pc, bold) in enumerate(t.rows):
                fill = NAVY_SOFT if bold else None
                cell(r0 + i, 0, label, bold, fill, align=PP_ALIGN.LEFT)
                for k, x in enumerate(v):
                    j = 1 + k * (2 if t.pct else 1)
                    cell(r0 + i, j, _fmt(x, number_format), bold, fill, RED if (x or 0) < -0.005 else (0x1B, 0x1B, 0x2A))
                    if t.pct:
                        cell(r0 + i, j + 1, _pct(pc[k]), False, fill, GREY, size=10)
            y += rh * nrows + Inches(0.25)
        if sl.note:
            text(s, Inches(0.5), prs.slide_height - Inches(0.75), Inches(9), Inches(0.3), sl.note, 10, False, GREY)
        text(s, Inches(0.5), prs.slide_height - Inches(0.45), Inches(9), Inches(0.3),
             f"{deck.company} · {deck.period} · {deck.run}", 9, False, GREY)
        text(s, Inches(11.8), prs.slide_height - Inches(0.45), Inches(1.0), Inches(0.3), str(n), 9, False, GREY, PP_ALIGN.RIGHT)

    prs.core_properties.title = f"Product costing – {deck.company} – {deck.period}"
    prs.core_properties.author = deck.company
    buf = io.BytesIO()
    prs.save(buf)
    return _mark_final(buf.getvalue())


def _mark_final(pptx: bytes) -> bytes:
    """Set PowerPoint's 'Mark as Final' flag so the file opens read-only."""
    src = zipfile.ZipFile(io.BytesIO(pptx))
    out = io.BytesIO()
    custom = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
              'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
              '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="_MarkAsFinal">'
              '<vt:bool>true</vt:bool></property></Properties>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml" and b"custom-properties+xml" not in data:
                data = data.replace(b"</Types>", b'<Override PartName="/docProps/custom.xml" ContentType="'
                                    b'application/vnd.openxmlformats-officedocument.custom-properties+xml"/></Types>')
            if item.filename == "_rels/.rels" and b"custom-properties" not in data:
                data = data.replace(b"</Relationships>", b'<Relationship Id="rIdCustom" Type="http://schemas.'
                                    b'openxmlformats.org/officeDocument/2006/relationships/custom-properties" '
                                    b'Target="docProps/custom.xml"/></Relationships>')
            if item.filename == "docProps/custom.xml":
                continue
            dst.writestr(item, data)
        dst.writestr("docProps/custom.xml", custom)
    return out.getvalue()


# --------------------------------------------------------------------------------------------- PDF
def deck_pdf(deck: Deck, number_format: str = "indian") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table as RTable, TableStyle
    from reportlab.lib.styles import ParagraphStyle

    W, H = 13.333 * inch, 7.5 * inch
    buf = io.BytesIO()
    navy = colors.Color(*(c / 255 for c in NAVY))
    soft = colors.Color(*(c / 255 for c in NAVY_SOFT))
    grey = colors.Color(*(c / 255 for c in GREY))
    red = colors.Color(*(c / 255 for c in RED))
    line = colors.Color(*(c / 255 for c in LINE))
    title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=22, textColor=navy, leading=26)
    sub = ParagraphStyle("s", fontName="Helvetica", fontSize=11, textColor=grey, leading=14)
    small = ParagraphStyle("n", fontName="Helvetica", fontSize=9, textColor=grey)
    stamp = dt.datetime.now().strftime("%d %b %Y")

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, H - 0.1 * inch, W, 0.1 * inch, stroke=0, fill=1)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(grey)
        canvas.drawString(0.5 * inch, 0.35 * inch, f"{deck.company} · {deck.period} · {deck.run}")
        canvas.drawRightString(W - 0.5 * inch, 0.35 * inch, str(doc.page))
        logo = _brand("logo-short.png")
        if doc.page > 1 and logo.exists():
            canvas.drawImage(str(logo), W - 1.05 * inch, H - 0.85 * inch, 0.55 * inch, 0.55 * inch, mask="auto")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=landscape((H, W)), leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                            topMargin=0.35 * inch, bottomMargin=0.6 * inch, title=f"Product costing – {deck.company}")
    story = []
    logo = _brand("logo-long.png")
    story.append(Spacer(1, 0.6 * inch))
    if logo.exists():
        story.append(Image(str(logo), width=2.9 * inch, height=0.75 * inch, hAlign="LEFT"))
    story += [Spacer(1, 1.1 * inch), Paragraph("Product costing – plant-wise performance", ParagraphStyle(
        "c", fontName="Helvetica-Bold", fontSize=30, textColor=navy, leading=34)), Spacer(1, 0.2 * inch),
        Paragraph(f"{deck.company} · {deck.period}", ParagraphStyle("c2", fontName="Helvetica", fontSize=16, textColor=grey)),
        Spacer(1, 2.2 * inch), Paragraph(f"{deck.run} · amounts in {deck.unit} · {stamp}", small), PageBreak()]

    for k, sl in enumerate(deck.slides):
        story += [Paragraph(sl.title, title), Paragraph(sl.subtitle, sub), Spacer(1, 0.15 * inch)]
        for t in sl.tables:
            head = [t.first_header]
            for c in t.columns:
                head += [c, ""] if t.pct else [c]
            data = [head]
            if t.pct:
                data.append([""] + ["Rs", "%"] * len(t.columns))
            for label, v, pc, bold in t.rows:
                row = [label]
                for i, x in enumerate(v):
                    row.append(_fmt(x, number_format))
                    if t.pct:
                        row.append(_pct(pc[i]))
                data.append(row)
            ncols = len(head)
            first = 2.4 * inch
            other = (W - 1.0 * inch - first) / (ncols - 1)
            rt = RTable(data, colWidths=[first] + [other] * (ncols - 1), repeatRows=1)
            st = [("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                  ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("ALIGN", (1, 0), (-1, 0), "CENTER"),
                  ("LINEBELOW", (0, 0), (-1, -1), 0.4, line), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
            if t.pct:
                for i in range(len(t.columns)):
                    st.append(("SPAN", (1 + 2 * i, 0), (2 + 2 * i, 0)))
                    st.append(("TEXTCOLOR", (2 + 2 * i, 2), (2 + 2 * i, -1), grey))
                st += [("BACKGROUND", (0, 1), (-1, 1), soft), ("TEXTCOLOR", (0, 1), (-1, 1), navy),
                       ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 8)]
            r0 = 2 if t.pct else 1
            for i, (_, v, _, bold) in enumerate(t.rows):
                if bold:
                    st += [("BACKGROUND", (0, r0 + i), (-1, r0 + i), soft), ("FONT", (0, r0 + i), (-1, r0 + i), "Helvetica-Bold", 9)]
                for j, x in enumerate(v):
                    if (x or 0) < -0.005:
                        st.append(("TEXTCOLOR", (1 + j * (2 if t.pct else 1), r0 + i), (1 + j * (2 if t.pct else 1), r0 + i), red))
            rt.setStyle(TableStyle(st))
            story += [rt, Spacer(1, 0.2 * inch)]
        if sl.note:
            story.append(Paragraph(sl.note, small))
        if k < len(deck.slides) - 1:
            story.append(PageBreak())
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()


def settings_format(company) -> str:
    return company_settings(company).get("number_format", "indian")
