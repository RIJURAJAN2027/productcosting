"""Periods and period inputs: uploads (TB, MIS, legacy workbook), adjustment rules,
material-cost adjustments, drivers, MIS lines; lock / unlock."""
from __future__ import annotations

import datetime as dt
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..audit import row_dict
from ..db import get_db
from ..deps import ensure_open, get_company, get_current_user, get_period, require
from ..engine import expr as ex
from ..models import (AdjustmentRule, Company, DriverValue, McAdjustment, MisLine, Period, TBLine, Upload, User,
                      now)
from ..services import uploads as up_svc
from ..services.legacy_import import import_legacy
from ..settings import get_settings

router = APIRouter(prefix="/api/companies/{company_id}", tags=["periods & inputs"])
ADJ_TYPES = {"regroup_gl", "move_dim", "exclude_gl", "amount", "reclass"}


class PeriodIn(BaseModel):
    code: str
    label: str = ""
    scenario: str = "actual"
    basis: str = "YTD"
    fiscal_year: str | None = None
    start_date: dt.date | None = None
    end_date: dt.date
    months: int | None = None


class AdjustmentIn(BaseModel):
    type: str
    definition: dict
    reason: str
    period_id: int | None = None      # None = standing rule for every period
    enabled: bool = True
    sort_order: int = 0


class McIn(BaseModel):
    plant_code: str
    pg_code: str
    kind: str = "amount"              # amount (rupees) | mc_pct (fraction)
    value: float
    reason: str


class DriverIn(BaseModel):
    driver: str
    values: dict[str, float]          # "PLANT|PG" -> value
    standing: bool = False


class MisIn(BaseModel):
    label: str
    expr: str
    mis_value: float
    compare: str = "sum"
    sort_order: int = 0


def _period_dict(p: Period, db: Session) -> dict:
    d = row_dict(p)
    ups = db.scalars(select(Upload).where(Upload.period_id == p.id, Upload.is_active.is_(True))).all()
    d["uploads"] = {u.kind: {"id": u.id, "filename": u.filename, "status": u.status,
                             "uploaded_at": u.created_at.isoformat() if u.created_at else None} for u in ups}
    return d


async def _read(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File larger than {get_settings().max_upload_mb} MB")
    return data


# ----------------------------------------------------------------------- periods
@router.get("/periods")
def list_periods(company: Company = Depends(get_company), db: Session = Depends(get_db)):
    rows = db.scalars(select(Period).where(Period.company_id == company.id)
                      .order_by(Period.end_date.desc(), Period.scenario)).all()
    return [_period_dict(p, db) for p in rows]


@router.post("/periods", status_code=201)
def create_period(body: PeriodIn, company: Company = Depends(get_company), user: User = Depends(require("edit")),
                  db: Session = Depends(get_db)):
    if body.scenario not in ("actual", "budget", "forecast"):
        raise HTTPException(422, "scenario must be actual, budget or forecast")
    if db.scalar(select(Period).where(Period.company_id == company.id, Period.code == body.code,
                                      Period.scenario == body.scenario)):
        raise HTTPException(409, "This period already exists")
    months = body.months
    if months is None:
        months = 1 if not body.start_date else (body.end_date.year - body.start_date.year) * 12 + \
            body.end_date.month - body.start_date.month + 1
    p = Period(company_id=company.id, code=body.code, label=body.label or body.code, scenario=body.scenario,
               basis=body.basis, fiscal_year=body.fiscal_year, start_date=body.start_date, end_date=body.end_date,
               months=months)
    db.add(p)
    db.flush()
    audit.log(db, user, "create", "period", p.id, company.id, code=p.code, scenario=p.scenario)
    db.commit()
    return _period_dict(p, db)


@router.get("/periods/{period_id}")
def get_period_info(period: Period = Depends(get_period), db: Session = Depends(get_db)):
    return _period_dict(period, db)


@router.post("/periods/{period_id}/lock")
def lock_period(period: Period = Depends(get_period), user: User = Depends(require("approve")),
                db: Session = Depends(get_db)):
    period.status, period.locked_by, period.locked_at = "locked", user.id, now()
    audit.log(db, user, "lock", "period", period.id, period.company_id, code=period.code)
    db.commit()
    return _period_dict(period, db)


@router.post("/periods/{period_id}/unlock")
def unlock_period(period: Period = Depends(get_period), user: User = Depends(require("admin")),
                  db: Session = Depends(get_db)):
    period.status, period.locked_by, period.locked_at = "open", None, None
    audit.log(db, user, "unlock", "period", period.id, period.company_id, code=period.code)
    db.commit()
    return _period_dict(period, db)


@router.delete("/periods/{period_id}")
def delete_period(period: Period = Depends(get_period), user: User = Depends(require("admin")),
                  db: Session = Depends(get_db)):
    ensure_open(period)
    audit.log(db, user, "delete", "period", period.id, period.company_id, code=period.code)
    db.delete(period)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------- uploads
@router.post("/periods/{period_id}/uploads", status_code=201)
async def upload(kind: str = Form(...), file: UploadFile = File(...), company: Company = Depends(get_company),
                 period: Period = Depends(get_period), user: User = Depends(require("edit")),
                 db: Session = Depends(get_db)):
    ensure_open(period)
    data = await _read(file)
    if kind in up_svc.KINDS:
        u = up_svc.upload_tb(db, company, period, kind, file.filename or "upload.xlsx", data, user)
        return {**row_dict(u), "issues": u.issues[:500]}
    if kind == "MIS":
        return up_svc.upload_mis(db, company, period, file.filename or "mis.xlsx", data, user)
    raise HTTPException(400, "kind must be BA_TB, CC_TB or MIS")


@router.get("/periods/{period_id}/uploads")
def list_uploads(period: Period = Depends(get_period), db: Session = Depends(get_db)):
    rows = db.scalars(select(Upload).where(Upload.period_id == period.id).order_by(Upload.id.desc())).all()
    return [{**row_dict(u), "issues": None, "issue_count": len(u.issues or []),
             "created_at": u.created_at.isoformat() if u.created_at else None} for u in rows]


@router.get("/uploads/{upload_id}")
def upload_detail(upload_id: int, company: Company = Depends(get_company), db: Session = Depends(get_db)):
    u = db.get(Upload, upload_id)
    if u is None or u.company_id != company.id:
        raise HTTPException(404, "Upload not found")
    return {**row_dict(u), "created_at": u.created_at.isoformat() if u.created_at else None}


@router.get("/uploads/{upload_id}/download")
def upload_download(upload_id: int, company: Company = Depends(get_company), db: Session = Depends(get_db)):
    """Re-create the uploaded TB in wide format (GL rows x business areas / cost centres)."""
    u = db.get(Upload, upload_id)
    if u is None or u.company_id != company.id:
        raise HTTPException(404, "Upload not found")
    rows = db.execute(select(TBLine.gl, TBLine.gl_desc, TBLine.dim, TBLine.amount)
                      .where(TBLine.upload_id == u.id)).all()
    import pandas as pd
    df = pd.DataFrame(rows, columns=["gl", "gl_desc", "dim", "amount"])
    wide = df.pivot_table(index=["gl", "gl_desc"], columns="dim", values="amount", aggfunc="sum", fill_value=0.0)
    wb = Workbook()
    ws = wb.active
    ws.title = "Input_BA TB" if u.kind == "BA_TB" else "Input_CC TB"
    ws["A1"] = f"{company.name} – {u.kind} – {u.filename}"
    dims = list(wide.columns)
    header = ["G/L Code", "G/L Description", "FS Description", "Total"] + dims
    for j, h in enumerate(header, start=1):
        ws.cell(3, j, h)
    for i, ((gl, desc), vals) in enumerate(wide.iterrows(), start=4):
        ws.cell(i, 1, int(gl) if str(gl).isdigit() else gl)
        ws.cell(i, 2, desc)
        ws.cell(i, 4, float(vals.sum()))
        for j, v in enumerate(vals.values, start=5):
            if v:
                ws.cell(i, j, float(v))
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{u.kind}_{u.id}.xlsx"'})


@router.post("/legacy-import")
async def legacy_import(file: UploadFile = File(...), period_code: str | None = Form(None),
                        period_label: str | None = Form(None), end_date: dt.date | None = Form(None),
                        start_date: dt.date | None = Form(None), company: Company = Depends(get_company),
                        user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    """Create this company's masters, rules, P&L structure and report layouts from a legacy workbook.
    With a period code the workbook's TBs, adjustments, MC adjustments and MIS figures are loaded too and
    the result is compared with the workbook (regression)."""
    data = await _read(file)
    try:
        return import_legacy(db, company, data, file.filename or "workbook.xlsx", user, period_code, period_label,
                             end_date, start_date)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/periods/{period_id}/derive-monthly")
def derive_monthly(ytd_current_id: int, ytd_previous_id: int | None = None, company: Company = Depends(get_company),
                   period: Period = Depends(get_period), user: User = Depends(require("edit")),
                   db: Session = Depends(get_db)):
    ensure_open(period)
    cur = db.get(Period, ytd_current_id)
    prev = db.get(Period, ytd_previous_id) if ytd_previous_id else None
    if cur is None or cur.company_id != company.id or (prev is not None and prev.company_id != company.id):
        raise HTTPException(404, "Source period not found")
    return {"uploads": up_svc.derive_monthly(db, company, period, cur, prev, user)}


# ----------------------------------------------------------------------- adjustments
def _adj_dict(a: AdjustmentRule) -> dict:
    d = row_dict(a)
    d["created_at"] = a.created_at.isoformat() if a.created_at else None
    return d


@router.get("/adjustments")
def list_adjustments(period_id: int | None = None, company: Company = Depends(get_company),
                     db: Session = Depends(get_db)):
    q = select(AdjustmentRule).where(AdjustmentRule.company_id == company.id)
    if period_id is not None:
        q = q.where(or_(AdjustmentRule.period_id == period_id, AdjustmentRule.period_id.is_(None)))
    return [_adj_dict(a) for a in db.scalars(q.order_by(AdjustmentRule.sort_order, AdjustmentRule.id)).all()]


def _check_adj(body: AdjustmentIn) -> None:
    if body.type not in ADJ_TYPES:
        raise HTTPException(422, f"type must be one of {sorted(ADJ_TYPES)}")
    if not body.reason.strip():
        raise HTTPException(422, "Every adjustment needs a reason")
    d = body.definition
    need = {"regroup_gl": ["from_gls", "to_gl"], "move_dim": ["gl", "to"], "exclude_gl": ["gls"],
            "amount": ["gl", "dim", "amount"], "reclass": ["from", "to", "amount"]}[body.type]
    missing = [k for k in need if k not in d]
    if missing:
        raise HTTPException(422, f"definition is missing {', '.join(missing)}")
    if d.get("tb", "BA") not in ("BA", "CC", "BOTH"):
        raise HTTPException(422, "tb must be BA, CC or BOTH")


@router.post("/adjustments", status_code=201)
def create_adjustment(body: AdjustmentIn, company: Company = Depends(get_company),
                      user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    _check_adj(body)
    if body.period_id:
        p = db.get(Period, body.period_id)
        if p is None or p.company_id != company.id:
            raise HTTPException(404, "Period not found")
        ensure_open(p)
    a = AdjustmentRule(company_id=company.id, period_id=body.period_id, type=body.type, definition=body.definition,
                       reason=body.reason, author_id=user.id, author_name=user.name or user.email,
                       enabled=body.enabled, sort_order=body.sort_order)
    db.add(a)
    db.flush()
    audit.log(db, user, "create", "adjustment", a.id, company.id, values=_adj_dict(a))
    db.commit()
    return _adj_dict(a)


@router.patch("/adjustments/{adj_id}")
def update_adjustment(adj_id: int, body: AdjustmentIn, company: Company = Depends(get_company),
                      user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    a = db.get(AdjustmentRule, adj_id)
    if a is None or a.company_id != company.id:
        raise HTTPException(404, "Adjustment not found")
    _check_adj(body)
    for pid in {a.period_id, body.period_id} - {None}:
        ensure_open(db.get(Period, pid))
    before = _adj_dict(a)
    a.type, a.definition, a.reason, a.period_id = body.type, body.definition, body.reason, body.period_id
    a.enabled, a.sort_order = body.enabled, body.sort_order
    audit.log(db, user, "update", "adjustment", a.id, company.id, changes=audit.diff(before, _adj_dict(a)))
    db.commit()
    return _adj_dict(a)


@router.delete("/adjustments/{adj_id}")
def delete_adjustment(adj_id: int, company: Company = Depends(get_company), user: User = Depends(require("edit")),
                      db: Session = Depends(get_db)):
    a = db.get(AdjustmentRule, adj_id)
    if a is None or a.company_id != company.id:
        raise HTTPException(404, "Adjustment not found")
    if a.period_id:
        ensure_open(db.get(Period, a.period_id))
    audit.log(db, user, "delete", "adjustment", a.id, company.id, values=_adj_dict(a))
    db.delete(a)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------- MC adjustments
@router.get("/periods/{period_id}/mc-adjustments")
def list_mc(period: Period = Depends(get_period), db: Session = Depends(get_db)):
    return [row_dict(m) for m in db.scalars(select(McAdjustment).where(McAdjustment.period_id == period.id)).all()]


@router.post("/periods/{period_id}/mc-adjustments", status_code=201)
def add_mc(body: McIn, company: Company = Depends(get_company), period: Period = Depends(get_period),
           user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    ensure_open(period)
    if body.kind not in ("amount", "mc_pct"):
        raise HTTPException(422, "kind must be amount or mc_pct")
    if not body.reason.strip():
        raise HTTPException(422, "A reason is required")
    m = McAdjustment(company_id=company.id, period_id=period.id, plant_code=body.plant_code, pg_code=body.pg_code,
                     kind=body.kind, value=body.value, reason=body.reason, author_id=user.id)
    db.add(m)
    db.flush()
    audit.log(db, user, "create", "mc_adjustment", m.id, company.id, values=row_dict(m))
    db.commit()
    return row_dict(m)


@router.delete("/periods/{period_id}/mc-adjustments/{mc_id}")
def delete_mc(mc_id: int, period: Period = Depends(get_period), user: User = Depends(require("edit")),
              db: Session = Depends(get_db)):
    ensure_open(period)
    m = db.get(McAdjustment, mc_id)
    if m is None or m.period_id != period.id:
        raise HTTPException(404, "Not found")
    audit.log(db, user, "delete", "mc_adjustment", m.id, period.company_id, values=row_dict(m))
    db.delete(m)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------- drivers
@router.get("/periods/{period_id}/drivers")
def list_drivers(period: Period = Depends(get_period), db: Session = Depends(get_db)):
    rows = db.scalars(select(DriverValue).where(DriverValue.company_id == period.company_id,
                                                or_(DriverValue.period_id == period.id,
                                                    DriverValue.period_id.is_(None)))).all()
    out: dict[str, dict] = {}
    for d in rows:
        out.setdefault(d.driver, {"standing": d.period_id is None, "values": {}})["values"][
            f"{d.plant_code}|{d.pg_code}"] = d.value
    return out


@router.put("/periods/{period_id}/drivers")
def put_driver(body: DriverIn, company: Company = Depends(get_company), period: Period = Depends(get_period),
               user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    ensure_open(period)
    pid = None if body.standing else period.id
    db.query(DriverValue).filter(DriverValue.company_id == company.id, DriverValue.driver == body.driver,
                                 DriverValue.period_id.is_(pid) if pid is None else DriverValue.period_id == pid
                                 ).delete(synchronize_session=False)
    for key, v in body.values.items():
        p, _, g = key.partition("|")
        db.add(DriverValue(company_id=company.id, period_id=pid, driver=body.driver, plant_code=p, pg_code=g,
                           value=float(v)))
    audit.log(db, user, "update", "driver", body.driver, company.id, period=period.code, cells=len(body.values))
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------- MIS lines
@router.get("/periods/{period_id}/mis")
def list_mis(period: Period = Depends(get_period), db: Session = Depends(get_db)):
    return [row_dict(m) for m in db.scalars(select(MisLine).where(MisLine.period_id == period.id)
                                            .order_by(MisLine.sort_order, MisLine.id)).all()]


@router.post("/periods/{period_id}/mis", status_code=201)
def add_mis(body: MisIn, company: Company = Depends(get_company), period: Period = Depends(get_period),
            user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    ensure_open(period)
    try:
        ex.parse(body.expr)
    except ex.ExpressionError as e:
        raise HTTPException(422, f"Formula error: {e}")
    m = MisLine(company_id=company.id, period_id=period.id, **body.model_dump())
    db.add(m)
    db.flush()
    audit.log(db, user, "create", "mis_line", m.id, company.id, values=row_dict(m))
    db.commit()
    return row_dict(m)


@router.delete("/periods/{period_id}/mis/{mis_id}")
def delete_mis(mis_id: int, period: Period = Depends(get_period), user: User = Depends(require("edit")),
               db: Session = Depends(get_db)):
    ensure_open(period)
    m = db.get(MisLine, mis_id)
    if m is None or m.period_id != period.id:
        raise HTTPException(404, "Not found")
    audit.log(db, user, "delete", "mis_line", m.id, period.company_id, values=row_dict(m))
    db.delete(m)
    db.commit()
    return {"ok": True}
