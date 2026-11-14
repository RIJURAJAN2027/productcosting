"""Excel import / export of masters (one sheet per master)."""
from __future__ import annotations

import datetime as dt
import io
import json

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..engine.config import code_str
from ..models import (AllocationRule, BusinessArea, Category, Company, CostCentre, GLAccount, PnlLine, Plant,
                      PreFunction, ProductGroup, User)

SPEC = {
    "Plants": (Plant, ["code", "name", "sort_order", "is_reporting"]),
    "ProductGroups": (ProductGroup, ["code", "name", "family", "sort_order"]),
    "BusinessAreas": (BusinessArea, ["code", "name", "plant_code", "pg_code", "family"]),
    "GLAccounts": (GLAccount, ["code", "description", "fs_description", "category", "gl_type", "note"]),
    "CostCentres": (CostCentre, ["code", "description", "business_area", "plant_code", "function",
                                  "function_name"]),
    "Categories": (Category, ["name", "sign", "group_name", "sort_order"]),
    "PREFunctions": (PreFunction, ["code", "name", "group", "mode"]),
    "AllocationRules": (AllocationRule, ["dataset", "category", "pool", "mode", "label", "definition", "note",
                                         "sort_order"]),
    "PnLLines": (PnlLine, ["code", "label", "expr", "sort_order", "attrs"]),
}
JSON_FIELDS = {"definition", "attrs"}
MERGE_KEYS = {"Plants": ("code",), "ProductGroups": ("code",), "BusinessAreas": ("code",), "GLAccounts": ("code",),
              "CostCentres": ("code",), "Categories": ("name",), "PREFunctions": ("code",),
              "AllocationRules": ("dataset", "category", "pool"), "PnLLines": ("code",)}
DEFAULT_FROM = dt.date(2000, 1, 1)
COMMON = ["effective_from", "effective_to"]
HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")


def export_masters(db: Session, company: Company) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    readme = wb.create_sheet("README")
    readme["A1"] = f"Masters of {company.name} – exported {dt.datetime.now():%d %b %Y %H:%M}"
    readme["A1"].font = Font(bold=True, size=13)
    notes = [
        "Each sheet is one master. Columns are matched by header name.",
        "effective_from / effective_to (yyyy-mm-dd) version a row; leave effective_to empty for 'current'.",
        "AllocationRules.definition and PnLLines.attrs are JSON; edit them in the application when possible.",
        "gl_type is NONPRE, PRE or EXCLUDED. Category sign: 1 = debit positive (costs), -1 = credit (income).",
        "Import with mode 'replace' replaces the whole sheet's master; 'merge' upserts by code.",
    ]
    for i, n in enumerate(notes, start=3):
        readme.cell(i, 1, n)
    readme.column_dimensions["A"].width = 120
    for sheet, (model, fields) in SPEC.items():
        ws = wb.create_sheet(sheet)
        cols = fields + COMMON
        for j, f in enumerate(cols, start=1):
            c = ws.cell(1, j, f)
            c.font, c.fill = Font(bold=True, color="FFFFFF"), HEAD_FILL
        rows = db.scalars(select(model).where(model.company_id == company.id).order_by(model.id)).all()
        for i, obj in enumerate(rows, start=2):
            for j, f in enumerate(cols, start=1):
                v = getattr(obj, f)
                if f in JSON_FIELDS:
                    v = json.dumps(v or {}, ensure_ascii=False)
                ws.cell(i, j, v)
        for j, f in enumerate(cols, start=1):
            ws.column_dimensions[ws.cell(1, j).column_letter].width = 60 if f in JSON_FIELDS | {"expr"} else 18
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _date(v):
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def import_masters(db: Session, company: Company, data: bytes, user: User | None, mode: str = "merge") -> dict:
    if mode not in ("merge", "replace"):
        raise HTTPException(400, "mode must be merge or replace")
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as e:
        raise HTTPException(422, f"Not a valid Excel file: {e}")
    result: dict[str, dict] = {}
    for sheet, (model, fields) in SPEC.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        idx = {h: i for i, h in enumerate(header) if h}
        key_fields = MERGE_KEYS[sheet]
        if mode == "replace":
            db.query(model).filter(model.company_id == company.id).delete(synchronize_session=False)
        existing = {}
        if mode == "merge":
            existing = {tuple(getattr(o, f) for f in key_fields) + (o.effective_from,): o
                        for o in db.scalars(select(model).where(model.company_id == company.id))}
        created = updated = 0
        errors = []
        for n, r in enumerate(rows[1:], start=2):
            if not any(v not in (None, "") for v in r):
                continue
            vals = {}
            for f in fields + COMMON:
                if f not in idx:
                    continue
                v = r[idx[f]]
                if f in JSON_FIELDS:
                    try:
                        v = json.loads(v) if v else {}
                    except json.JSONDecodeError as e:
                        errors.append(f"{sheet} row {n}: invalid JSON in {f}: {e}")
                        continue
                elif f in ("effective_from", "effective_to"):
                    v = _date(v)
                elif f in ("code", "plant_code", "pg_code", "business_area", "function"):
                    v = code_str(v) or None
                elif f in ("sign", "sort_order"):
                    v = int(v) if v not in (None, "") else 0
                elif f == "is_reporting":
                    v = str(v).strip().lower() not in ("false", "0", "no", "")
                col = model.__table__.columns.get(f)
                if v is None and col is not None and not col.nullable:
                    if getattr(col.type, "python_type", None) is str:
                        v = ""
                    else:
                        continue          # keep the default / existing value
                vals[f] = v
            if vals.get("effective_from") is None:
                vals.pop("effective_from", None)
            missing = [f for f in key_fields if not vals.get(f)]
            if missing:
                errors.append(f"{sheet} row {n}: missing {', '.join(missing)}")
                continue
            k = tuple(vals.get(f) for f in key_fields) + (vals.get("effective_from") or DEFAULT_FROM,)
            obj = existing.get(k)
            if obj is None:
                db.add(model(company_id=company.id, **vals))
                created += 1
            else:
                for f, v in vals.items():
                    setattr(obj, f, v)
                updated += 1
        result[sheet] = {"created": created, "updated": updated, "errors": errors[:50]}
    audit.log(db, user, "masters_import", "masters", None, company.id, mode=mode, result=result)
    db.commit()
    return result
