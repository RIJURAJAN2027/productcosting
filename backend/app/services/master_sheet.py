"""The "Master" sheet: business areas, GL accounts and cost centres side by side, in the layout of the
costing workbook's Masters sheet::

    BA | Plant | COM/COR/PG | Product family | · | GL | GL Descript | Category1 | · |
    Cost centre | Cost centre descrip | BArea | Function | Allocation logic

The three blocks are independent lists that only share the sheet.  The sheet can be edited in the
browser or uploaded again as Excel.  An upload *merges*: new codes are added, changed values are updated,
blank cells never clear a value, and codes missing from the file are kept unless removal is asked for.

Every change can take effect from a date.  Rows changed or removed from that date are closed the day
before and a new version starts on the date, so calculations of earlier periods keep the mapping they
used and the next periods pick up the new one automatically.
"""
from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..engine.config import code_str, norm
from ..models import (BusinessArea, Category, Company, CostCentre, GLAccount, Plant, PreFunction, ProductGroup, Period,
                      User)
from .config_builder import active, company_settings

DEFAULT_FROM = dt.date(2000, 1, 1)
EXCLUDED_LABEL = "Excluded"


@dataclass(frozen=True)
class Col:
    field: str
    header: str
    width: int


# section -> (model, columns); the first column is the code (row identity)
SECTIONS: dict[str, tuple[type, list[Col]]] = {
    "ba": (BusinessArea, [Col("code", "BA", 14), Col("plant_code", "Plant", 10),
                          Col("pg_code", "COM/ COR/ PG", 12), Col("family", "Product family", 16)]),
    "gl": (GLAccount, [Col("code", "GL", 12), Col("description", "GL Descript", 36),
                       Col("category", "Category1", 26)]),
    "cc": (CostCentre, [Col("code", "Cost centre", 13), Col("description", "Cost centre descrip", 32),
                        Col("business_area", "BArea", 13), Col("function_name", "Function", 18),
                        Col("function", "Allocation logic", 16)]),
}
SECTION_TITLES = {"ba": "Business areas", "gl": "GL accounts", "cc": "Cost centres"}
CODE_FIELDS = {"code", "plant_code", "pg_code", "business_area", "function"}
# sheet columns (1-based); blank separator columns E and I as in the workbook
LAYOUT = {"ba": 1, "gl": 6, "cc": 10}


# ------------------------------------------------------------------------------------------- helpers
def _clean(f: str, v: Any) -> str | None:
    if v is None:
        return None
    if f in CODE_FIELDS:
        s = code_str(v)
    else:
        s = " ".join(str(v).split())
    if s in ("", "#N/A", "0", "nan", "None") and f != "description":
        return None
    return s[:300] or None


def _gl_category_out(g: GLAccount) -> str | None:
    return EXCLUDED_LABEL if g.gl_type == "EXCLUDED" and not g.category else g.category


def row_out(section: str, obj) -> dict:
    _, cols = SECTIONS[section]
    out = {c.field: getattr(obj, c.field) for c in cols}
    out["id"] = obj.id
    out["effective_from"] = obj.effective_from.isoformat() if obj.effective_from else None
    out["effective_to"] = obj.effective_to.isoformat() if obj.effective_to else None
    if section == "gl":
        out["category"] = _gl_category_out(obj)
        out["gl_type"] = obj.gl_type
        out["fs_description"] = obj.fs_description
    if section == "cc":
        out["plant_code"] = obj.plant_code
    out["missing"] = missing_fields(section, out)
    return out


def missing_fields(section: str, r: dict) -> list[str]:
    """Mapping fields a row still needs before the engine can use it."""
    if section == "ba":
        return [f for f in ("plant_code", "pg_code") if not r.get(f)]
    if section == "gl":
        return [] if r.get("category") else ["category"]
    return [f for f in ("business_area", "function") if not r.get(f)]


def current_rows(db: Session, company: Company, section: str, on: dt.date | None) -> dict[str, Any]:
    """Rows valid on ``on`` (or the latest version of every code when ``on`` is None), by code."""
    model, _ = SECTIONS[section]
    if on is not None:
        q = active(model, company.id, on)
    else:
        q = select(model).where(model.company_id == company.id, model.effective_to.is_(None))
    out: dict[str, Any] = {}
    for o in db.scalars(q.order_by(model.effective_from)).all():
        out[o.code] = o          # the latest version wins
    return out


def _pre_categories(company: Company) -> set[str]:
    return {norm(c) for c in company_settings(company).get("pre_categories", [])}


def _plant_lookup(db: Session, company: Company, on: dt.date | None) -> tuple[dict[str, str], dict[str, str]]:
    bas = current_rows(db, company, "ba", on)
    exact = {c: b.plant_code for c, b in bas.items() if b.plant_code}
    prefix: dict[str, str] = {}
    for c, p in exact.items():
        prefix.setdefault(c[:4], p)
    return exact, prefix


def _derive(section: str, vals: dict, ctx: dict) -> None:
    """Fields the engine needs that the sheet does not show: GL type and cost-centre plant."""
    if section == "gl" and "category" in vals:
        cat = vals["category"]
        if cat and norm(cat) in (norm(EXCLUDED_LABEL), "exclude", "excl"):
            vals["category"], vals["gl_type"] = None, "EXCLUDED"
        elif cat:
            vals["gl_type"] = "PRE" if norm(cat) in ctx["pre_categories"] else "NONPRE"
        else:
            vals["gl_type"] = "NONPRE"      # still to be mapped: shows as "needs mapping"
    if section == "cc" and "business_area" in vals:
        ba = vals["business_area"]
        plant = None
        if ba:
            plant = ctx["ba_plant"].get(ba) or ctx["ba_prefix"].get(ba[:4])
        if plant or not ba:
            vals["plant_code"] = plant


def _context(db: Session, company: Company, on: dt.date | None) -> dict:
    exact, prefix = _plant_lookup(db, company, on)
    return {"pre_categories": _pre_categories(company), "ba_plant": exact, "ba_prefix": prefix}


# ------------------------------------------------------------------------------------------- read
def options(db: Session, company: Company, on: dt.date | None) -> dict:
    s = company_settings(company)
    plants = [p.code for p in db.scalars(active(Plant, company.id, on).order_by(Plant.sort_order, Plant.code))]
    pgs = db.scalars(active(ProductGroup, company.id, on).order_by(ProductGroup.sort_order, ProductGroup.code)).all()
    fns = [f.code for f in db.scalars(active(PreFunction, company.id, on))]
    rows = {sec: current_rows(db, company, sec, on) for sec in SECTIONS}
    cats = sorted({g.category for g in rows["gl"].values() if g.category} | set(s.get("pre_categories", [])) |
                  {c.name for c in db.scalars(active(Category, company.id, on))}, key=str.casefold)
    families = sorted({p.family for p in pgs if p.family} | {b.family for b in rows["ba"].values() if b.family},
                      key=str.casefold)
    return {
        "plants": plants,
        "pg_codes": [s["com_code"], s["cor_code"]] + [p.code for p in pgs],
        "families": families,
        "categories": cats + [EXCLUDED_LABEL],
        "bareas": sorted(rows["ba"]),
        "functions": sorted({c.function_name for c in rows["cc"].values() if c.function_name}, key=str.casefold),
        "allocation": [s["com_code"], s["cor_code"]] + [p.code for p in pgs] + sorted(set(fns) - {s["com_code"],
                                                                                             s["cor_code"]}),
    }


def read_sheet(db: Session, company: Company, on: dt.date | None) -> dict:
    sections = {}
    for sec in SECTIONS:
        rows = sorted(current_rows(db, company, sec, on).values(), key=lambda o: o.code)
        sections[sec] = [row_out(sec, o) for o in rows]
    stats = {sec: {"rows": len(r), "missing": sum(1 for x in r if x["missing"])} for sec, r in sections.items()}
    stats["gl"]["by_type"] = {}
    for g in sections["gl"]:
        stats["gl"]["by_type"][g["gl_type"]] = stats["gl"]["by_type"].get(g["gl_type"], 0) + 1
    return {"on": on.isoformat() if on else None, "sections": sections, "stats": stats,
            "columns": {sec: [{"field": c.field, "header": c.header} for c in cols]
                        for sec, (_, cols) in SECTIONS.items()},
            "options": options(db, company, on)}


def pending_from_uploads(db: Session, company: Company, period: Period, on: dt.date | None) -> dict:
    """Codes in the period's active BA / CC trial balances that are not in the master yet."""
    from .runs import active_upload, tb_frame
    have = {sec: set(current_rows(db, company, sec, on)) for sec in SECTIONS}
    new: dict[str, dict[str, dict]] = {"ba": {}, "gl": {}, "cc": {}}
    for kind, dim, sec in (("BA_TB", "ba", "ba"), ("CC_TB", "cc", "cc")):
        up = active_upload(db, period, kind)
        if up is None:
            continue
        df = tb_frame(db, up.id, dim)
        if df.empty:
            continue
        for code, amt in df.groupby(dim)["amount"].apply(lambda s: float(s.abs().sum())).items():
            code = code_str(code)
            if code and code not in have[sec]:
                new[sec].setdefault(code, {"code": code, "amount": 0.0, "source": kind})["amount"] += amt
        g = df.groupby("gl").agg(gl_desc=("gl_desc", "first"), amount=("amount", lambda s: float(s.abs().sum())))
        for code, r in g.iterrows():
            code = code_str(code)
            if code and code not in have["gl"]:
                e = new["gl"].setdefault(code, {"code": code, "description": r.gl_desc or "", "amount": 0.0,
                                                "source": kind})
                e["amount"] += r.amount
    return {sec: sorted(v.values(), key=lambda x: -x["amount"]) for sec, v in new.items()}


# ------------------------------------------------------------------------------------------- write
def apply_changes(db: Session, company: Company, user: User | None, section: str, upserts: list[dict],
                  deletes: list[str], effective_from: dt.date | None, blank_keeps: bool,
                  source: str = "edit") -> dict:
    """Upsert / remove rows of one section.  With ``effective_from`` a change to a row that started
    earlier closes it the day before and starts a new version; otherwise rows change in place."""
    if section not in SECTIONS:
        raise HTTPException(404, f"Unknown section {section}")
    model, cols = SECTIONS[section]
    fields = [c.field for c in cols]
    cur = current_rows(db, company, section, effective_from)
    ctx = _context(db, company, effective_from)
    start = effective_from or DEFAULT_FROM
    added, changed, removed, unchanged = [], [], [], 0
    seen: set[str] = set()

    def new_version(obj, vals: dict) -> None:
        data = {c.name: getattr(obj, c.name) for c in model.__table__.columns
                if c.name not in ("id", "company_id", "created_at", "updated_at")}
        data.update(vals, effective_from=start, effective_to=obj.effective_to)
        obj.effective_to = start - dt.timedelta(days=1)
        db.add(model(company_id=company.id, **data))

    for raw in upserts:
        code = _clean("code", raw.get("code"))
        if not code:
            continue
        if code in seen:
            continue
        seen.add(code)
        vals: dict[str, Any] = {}
        for f in fields[1:]:
            if f not in raw:
                continue
            v = _clean(f, raw[f])
            if v is None and blank_keeps:
                continue
            vals[f] = v
        obj = cur.get(code)
        if section == "gl" and "category" in vals and obj is not None and vals["category"] == _gl_category_out(obj):
            vals.pop("category")          # unchanged – keep the stored GL type as it is
        _derive(section, vals, ctx)
        if obj is None:
            if section == "gl":
                vals.setdefault("gl_type", "NONPRE")
                vals.setdefault("description", "")
            if section == "cc":
                vals.setdefault("description", "")
            db.add(model(company_id=company.id, code=code, effective_from=start, **vals))
            added.append(code)
            continue
        diff = {f: v for f, v in vals.items() if getattr(obj, f) != v}
        if not diff:
            unchanged += 1
            continue
        before = {f: getattr(obj, f) for f in diff}
        if effective_from is None or obj.effective_from >= effective_from:
            for f, v in diff.items():
                setattr(obj, f, v)
        else:
            new_version(obj, diff)
        changed.append({"code": code, "before": before, "after": diff})

    for code in deletes:
        code = _clean("code", code)
        obj = cur.get(code) if code else None
        if obj is None:
            continue
        if effective_from is None or obj.effective_from >= effective_from:
            db.delete(obj)
        else:
            obj.effective_to = effective_from - dt.timedelta(days=1)
        removed.append(code)

    # a changed BA plant moves the cost centres booked to that BA
    if section == "ba" and (added or changed):
        db.flush()
        ctx = _context(db, company, effective_from)
        ccs = current_rows(db, company, "cc", effective_from)
        moved = []
        for cc in ccs.values():
            vals = {"business_area": cc.business_area}
            _derive("cc", vals, ctx)
            if "plant_code" in vals and vals["plant_code"] != cc.plant_code:
                if effective_from is None or cc.effective_from >= effective_from:
                    cc.plant_code = vals["plant_code"]
                else:
                    new_version(cc, {"plant_code": vals["plant_code"]})
                moved.append(cc.code)
        if moved:
            changed.append({"code": f"{len(moved)} cost centre(s)", "before": {}, "after": {"plant_code": "re-derived"}})

    result = {"section": section, "added": added, "changed": changed, "removed": removed, "unchanged": unchanged,
              "effective_from": effective_from.isoformat() if effective_from else None}
    if added or changed or removed:
        audit.log(db, user, f"master_sheet_{source}", "master_sheet", section, company.id,
                  effective_from=result["effective_from"], added=added[:200], removed=removed[:200],
                  changed=[{"code": c["code"], "after": c["after"]} for c in changed[:200]])
    return result


# ------------------------------------------------------------------------------------------- Excel
HEAD_FILL = PatternFill("solid", fgColor="0B3D6E")
BAND_FILL = PatternFill("solid", fgColor="E7F0FB")
THIN = Side(style="thin", color="C9D1DE")


def export_xlsx(db: Session, company: Company, on: dt.date | None) -> bytes:
    data = read_sheet(db, company, on)["sections"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Masters"
    for sec, start in LAYOUT.items():
        _, cols = SECTIONS[sec]
        band = ws.cell(1, start, SECTION_TITLES[sec])
        band.font, band.fill = Font(bold=True, color="0B3D6E"), BAND_FILL
        ws.merge_cells(start_row=1, start_column=start, end_row=1, end_column=start + len(cols) - 1)
        for j, c in enumerate(cols):
            h = ws.cell(2, start + j, c.header)
            h.font, h.fill = Font(bold=True, color="FFFFFF"), HEAD_FILL
            h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            h.border = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)
            ws.column_dimensions[h.column_letter].width = c.width
            for i, r in enumerate(data[sec], start=3):
                v = r.get(c.field)
                if c.field == "code" and v is not None and str(v).isdigit() and len(str(v)) < 16:
                    v = int(v)          # numeric codes stay numbers, as in the TB exports
                ws.cell(i, start + j, v)
    for sep in (5, 9):
        ws.column_dimensions[ws.cell(2, sep).column_letter].width = 3
    ws.row_dimensions[2].height = 32
    last = max(len(v) for v in data.values()) + 2
    ws.auto_filter.ref = f"A2:N{max(last, 2)}"
    ws.freeze_panes = "A3"
    info = wb.create_sheet("How to use")
    lines = [
        f"Master sheet of {company.name} – exported {dt.datetime.now():%d %b %Y %H:%M}"
        + (f", valid on {on:%d %b %Y}" if on else ""),
        "",
        "Business areas (A–D), GL accounts (F–H) and cost centres (J–N) are three separate lists.",
        "Upload this file again on the Master page. Rows are matched by code: new codes are added and changed "
        "values updated.",
        "Blank cells never clear a value, and codes missing from the file are kept unless you choose to remove them.",
        "You can upload a file that holds only the new rows (add-ons).",
        f"Category1 '{EXCLUDED_LABEL}' excludes a GL from the P&L. A category in the PRE list makes the GL a PRE "
        "(people-related) GL.",
        "Allocation logic is the product group, COM, COR or PRE function a cost centre's costs go to.",
        "The plant of a cost centre comes from its BArea.",
    ]
    for i, t in enumerate(lines, start=1):
        info.cell(i, 1, t)
    info["A1"].font = Font(bold=True, size=13)
    info.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADER_MATCH = {
    "ba": {"code": lambda h: h in ("ba", "business area", "business area code", "ba code"),
           "plant_code": lambda h: h in ("plant", "plant code"),
           "pg_code": lambda h: ("com" in h and "cor" in h) or h in ("pg", "product group", "pg code"),
           "family": lambda h: "family" in h},
    "gl": {"code": lambda h: h in ("gl", "g/l", "gl code", "g/l code", "gl account", "g/l account"),
           "description": lambda h: h.startswith(("gl desc", "g/l desc")),
           "category": lambda h: h.startswith("category")},
    "cc": {"code": lambda h: h.startswith(("cost centre", "cost center")) and "desc" not in h,
           "description": lambda h: h.startswith(("cost centre desc", "cost center desc", "cc desc")),
           "business_area": lambda h: h in ("barea", "b area", "b.area", "business area of cc"),
           "function_name": lambda h: h == "function" or h == "function name",
           "function": lambda h: h.startswith("allocation")},
}


def _hkey(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v).replace("\n", " ")).strip().casefold() if v is not None else ""


@dataclass
class ParsedSheet:
    rows: dict[str, list[dict]] = field(default_factory=lambda: {"ba": [], "gl": [], "cc": []})
    header_row: int = 0
    sheet: str = ""
    columns: dict[str, dict[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def parse_xlsx(data: bytes) -> ParsedSheet:
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Not a valid Excel file: {e}")
    names = sorted(wb.sheetnames, key=lambda n: 0 if norm(n) in ("masters", "master") else 1)
    for name in names:
        ws = wb[name]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        for hi, r in enumerate(rows[:15]):
            heads = [_hkey(v) for v in r]
            cols: dict[str, dict[str, int]] = {}
            for sec, matchers in HEADER_MATCH.items():
                found: dict[str, int] = {}
                for f, m in matchers.items():
                    for j, h in enumerate(heads):
                        if h and m(h) and j not in found.values():
                            found[f] = j
                            break
                if "code" in found:
                    cols[sec] = found
            if len(cols) == 0 or (len(cols) == 1 and len(next(iter(cols.values()))) < 2):
                continue
            out = ParsedSheet(header_row=hi + 1, sheet=name,
                              columns={s: {f: str(r[j]).replace("\n", " ") for f, j in c.items()}
                                       for s, c in cols.items()})
            for sec in SECTIONS:
                if sec not in cols:
                    out.warnings.append(f"No {SECTION_TITLES[sec].lower()} columns found – that list is unchanged.")
            for r2 in rows[hi + 1:]:
                for sec, c in cols.items():
                    code = _clean("code", r2[c["code"]] if c["code"] < len(r2) else None)
                    if not code or norm(code) in ("cost centre", "ba", "gl"):
                        continue
                    out.rows[sec].append({f: (r2[j] if j < len(r2) else None) for f, j in c.items()} | {"code": code})
            for sec, rs in out.rows.items():
                codes = [r["code"] for r in rs]
                dups = sorted({c for c in codes if codes.count(c) > 1})
                if dups:
                    out.warnings.append(f"{SECTION_TITLES[sec]}: {len(dups)} code(s) appear more than once "
                                        f"({', '.join(dups[:8])}{'…' if len(dups) > 8 else ''}); the first row is used.")
            return out
    raise HTTPException(422, "No master header found. The sheet needs headers such as BA, Plant, GL, Category1, "
                             "Cost centre, BArea and Allocation logic.")


def preview_or_import(db: Session, company: Company, user: User | None, data: bytes, filename: str,
                      effective_from: dt.date | None, remove_missing: bool, dry_run: bool) -> dict:
    parsed = parse_xlsx(data)
    result: dict[str, Any] = {"filename": filename, "sheet": parsed.sheet, "header_row": parsed.header_row,
                              "columns": parsed.columns, "warnings": list(parsed.warnings), "sections": {},
                              "dry_run": dry_run, "effective_from": effective_from.isoformat() if effective_from
                              else None}
    opts = options(db, company, effective_from)
    for sec in SECTIONS:
        if sec not in parsed.columns:
            continue
        rows = parsed.rows[sec]
        file_codes = {r["code"] for r in rows}
        cur = current_rows(db, company, sec, effective_from)
        missing = sorted(set(cur) - file_codes)
        res = apply_changes(db, company, user, sec, rows, missing if remove_missing else [], effective_from,
                            blank_keeps=True, source="upload")
        res["not_in_file"] = len(missing) if not remove_missing else 0
        res["file_rows"] = len(rows)
        result["sections"][sec] = res
    # values the engine will not recognise
    unknown = []
    for r in parsed.rows["ba"]:
        p, g = _clean("plant_code", r.get("plant_code")), _clean("pg_code", r.get("pg_code"))
        if p and p not in opts["plants"]:
            unknown.append(f"BA {r['code']}: plant {p} is not a reporting plant")
        if g and g not in opts["pg_codes"]:
            unknown.append(f"BA {r['code']}: {g} is not COM, COR or a product group")
    for r in parsed.rows["cc"]:
        f = _clean("function", r.get("function"))
        if f and f not in opts["allocation"]:
            unknown.append(f"Cost centre {r['code']}: allocation logic {f} is not a product group, COM, COR or "
                           "PRE function")
    if unknown:
        result["warnings"].append(f"{len(unknown)} value(s) are not set up in Masters (plants, product groups or "
                                  "PRE functions) – add them there or the calculation reports them.")
        result["unknown"] = unknown[:200]
    if dry_run:
        db.rollback()
    else:
        audit.log(db, user, "master_sheet_upload_file", "master_sheet", None, company.id, filename=filename,
                  effective_from=result["effective_from"], remove_missing=remove_missing,
                  counts={s: {k: len(v) if isinstance(v, list) else v for k, v in r.items()
                              if k in ("added", "changed", "removed", "unchanged")}
                          for s, r in result["sections"].items()})
        db.commit()
    return result


def effective_date(v: str | None) -> dt.date | None:
    if not v or v in ("all", "none", "null"):
        return None
    try:
        return dt.date.fromisoformat(v[:10])
    except ValueError:
        raise HTTPException(422, "effective_from must be a date (yyyy-mm-dd) or 'all'")

