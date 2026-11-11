"""Calculation runs, approval workflow, reports, exports, drill-down, comparisons, dashboard, audit."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import row_dict
from ..db import get_db
from ..deps import ensure_open, get_company, get_current_user, get_period, get_run_for_user, require
from ..models import AuditLog, CalcRun, Company, Period, User
from ..services import exports, reports
from ..services.config_builder import UNITS, company_settings
from ..services.runs import execute_run, transition

router = APIRouter(prefix="/api", tags=["runs & reports"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class RunIn(BaseModel):
    note: str | None = None


class TransitionIn(BaseModel):
    action: str
    comment: str | None = None


def run_dict(run: CalcRun, db: Session, full: bool = False) -> dict:
    period = db.get(Period, run.period_id)
    names = {}
    for uid in {run.started_by, run.submitted_by, run.approved_by} - {None}:
        u = db.get(User, uid)
        names[uid] = (u.name or u.email) if u else None
    d = {"id": run.id, "company_id": run.company_id, "period_id": run.period_id, "period_code": period.code,
         "period_label": period.label, "scenario": period.scenario, "status": run.status, "note": run.note,
         "created_at": run.created_at.isoformat() if run.created_at else None,
         "finished_at": run.finished_at.isoformat() if run.finished_at else None, "duration_s": run.duration_s,
         "control_summary": run.control_summary, "error": run.error,
         "started_by": names.get(run.started_by), "submitted_by": names.get(run.submitted_by),
         "submitted_at": run.submitted_at.isoformat() if run.submitted_at else None,
         "approved_by": names.get(run.approved_by),
         "approved_at": run.approved_at.isoformat() if run.approved_at else None,
         "review_comment": run.review_comment, "timings": run.timings}
    if full:
        d["controls"] = run.controls
        d["inputs"] = {k: v for k, v in (run.inputs_snapshot or {}).items() if k != "drivers"}
        d["plants"] = run.config_snapshot.get("plants")
        d["product_groups"] = run.config_snapshot.get("product_groups")
        d["com_code"] = run.config_snapshot.get("com_code")
        d["calc_unit_divisor"] = run.config_snapshot.get("calc_unit_divisor")
    return d


def _run(run_id: int, user: User, db: Session) -> CalcRun:
    run = get_run_for_user(run_id, user, db)
    if run.status == "failed":
        raise HTTPException(409, f"Run {run_id} failed: {run.error}")
    return run


# ----------------------------------------------------------------------- runs
@router.post("/companies/{company_id}/periods/{period_id}/runs", status_code=201)
def calculate(body: RunIn | None = None, company: Company = Depends(get_company),
              period: Period = Depends(get_period), user: User = Depends(require("edit")),
              db: Session = Depends(get_db)):
    ensure_open(period)
    run = execute_run(db, company, period, user, body.note if body else None)
    return run_dict(run, db, full=True)


@router.get("/companies/{company_id}/runs")
def list_runs(period_id: int | None = None, company: Company = Depends(get_company), db: Session = Depends(get_db),
              limit: int = 100):
    q = select(CalcRun).where(CalcRun.company_id == company.id)
    if period_id:
        q = q.where(CalcRun.period_id == period_id)
    return [run_dict(r, db) for r in db.scalars(q.order_by(CalcRun.id.desc()).limit(limit)).all()]


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return run_dict(get_run_for_user(run_id, user, db), db, full=True)


@router.post("/runs/{run_id}/transition")
def run_transition(run_id: int, body: TransitionIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    from ..models import Tenant
    run = _run(run_id, user, db)
    perm = "edit" if body.action == "submit" else "approve"
    if not (user.is_superadmin or user.role in {"edit": {"analyst", "admin"}, "approve": {"approver", "admin"}}[perm]):
        raise HTTPException(403, f"Your role ({user.role}) may not {body.action} runs.")
    period = db.get(Period, run.period_id)
    if body.action != "submit":
        ensure_open(period)
    user._tenant_settings = (db.get(Tenant, user.tenant_id).settings or {})   # type: ignore[attr-defined]
    return run_dict(transition(db, run, body.action, user, body.comment), db)


# ----------------------------------------------------------------------- reports
@router.get("/reports")
def report_catalogue():
    return {"reports": reports.REPORTS, "units": list(UNITS),
            "extra": {"mc": "Material cost %", "mc_comparison": "MC % across periods", "mis": "Check with MIS",
                      "summary": "Management summary", "comparison": "Period / budget comparison"}}


@router.get("/runs/{run_id}/reports/{name}")
def report(run_id: int, name: str, plant: str | None = None, pg: str | None = None, unit: str | None = None,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = _run(run_id, user, db)
    if name == "mc":
        return reports.mc_report(db, run, unit)
    if name == "mis":
        return reports.mis_check(db, run, unit)
    if name == "summary":
        return reports.management_summary(db, run, unit)
    return reports.build_report(db, run, name, plant, pg, unit)


def _export_name(company: Company, period: Period, name: str, ext: str) -> str:
    return f"{company.code}_{period.code}_{name}.{ext}".replace(" ", "_")


@router.get("/runs/{run_id}/export/{name}.xlsx")
def export_xlsx(run_id: int, name: str, plant: str | None = None, pg: str | None = None, unit: str | None = None,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = _run(run_id, user, db)
    company, period = db.get(Company, run.company_id), db.get(Period, run.period_id)
    s = company_settings(company)
    nf, dec = s["number_format"], int(s["decimals"])
    if name == "pack":
        data = _full_pack(db, run, unit, nf, dec)
    elif name == "mc":
        headers, rows, fmts = exports.mc_rows(reports.mc_report(db, run, unit))
        data = exports.report_workbook([], nf, dec, extra=[("MC %", "Material cost %", headers, rows, fmts)])
    elif name == "mis":
        headers, rows, fmts = exports.mis_rows(reports.mis_check(db, run, unit))
        data = exports.report_workbook([], nf, dec, extra=[("Check with MIS", "Check with MIS", headers, rows,
                                                            fmts)])
    else:
        rep = reports.build_report(db, run, name, plant, pg, unit)
        data = exports.report_workbook([(rep["title"], rep)], nf, dec)
    return Response(data, media_type=XLSX, headers={
        "Content-Disposition": f'attachment; filename="{_export_name(company, period, name, "xlsx")}"'})


def _full_pack(db: Session, run: CalcRun, unit: str | None, nf: str, dec: int) -> bytes:
    """All reports in one workbook, mirroring the legacy report section."""
    company, period, cfg = reports.run_context(db, run)
    reps = [("Plant-wise PL", reports.build_report(db, run, "plant_wise", unit=unit))]
    for p in cfg.plants:
        reps.append((f"PG-wise PL_{p}", reports.build_report(db, run, "pg_wise", plant=p, unit=unit)))
    reps.append(("All PG", reports.build_report(db, run, "all_pg", unit=unit)))
    for g in cfg.product_groups:
        reps.append((f"PG Totals_{g}", reports.build_report(db, run, "pg_totals", pg=g, unit=unit)))
    extra = []
    try:
        h, r, f = exports.mc_rows(reports.mc_report(db, run, unit))
        extra.append(("MC %", "Material cost %", h, r, f))
    except HTTPException:
        pass
    h, r, f = exports.mis_rows(reports.mis_check(db, run, unit))
    extra.append(("Check with MIS", "Check with MIS", h, r, f))
    div = cfg.calc_unit_divisor / UNITS[unit or company_settings(company)["display_unit"]]
    h, r, f = exports.controls_rows(run.controls or [], div)
    extra.append(("Controls", f"Controls – run {run.id} ({run.status})", h, r, f))
    return exports.report_workbook(reps, nf, dec, extra=extra)


@router.get("/runs/{run_id}/export/{name}.pdf")
def export_pdf(run_id: int, name: str, plant: str | None = None, pg: str | None = None, unit: str | None = None,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = _run(run_id, user, db)
    company, period = db.get(Company, run.company_id), db.get(Period, run.period_id)
    rep = reports.build_report(db, run, name, plant, pg, unit)
    return Response(exports.report_pdf(rep), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{_export_name(company, period, name, "pdf")}"'})


@router.get("/runs/{run_id}/drilldown")
def drilldown(run_id: int, line: str, plant: str, code: str = "__TOTAL__", unit: str | None = None,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports.drilldown(db, _run(run_id, user, db), line, plant, code, unit)


@router.get("/companies/{company_id}/mc-comparison")
def mc_comparison(period_ids: list[int] = Query(...), unit: str | None = None,
                  company: Company = Depends(get_company), db: Session = Depends(get_db)):
    return reports.mc_comparison(db, company, period_ids, unit)


@router.get("/compare")
def compare(run_a: int, run_b: int, level: str = "company", plant: str | None = None, pg: str | None = None,
            unit: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return reports.compare_runs(db, _run(run_a, user, db), _run(run_b, user, db), level, plant, pg, unit)


@router.get("/companies/{company_id}/dashboard")
def dashboard(period_id: int | None = None, unit: str | None = None, company: Company = Depends(get_company),
              db: Session = Depends(get_db)):
    return reports.dashboard(db, company, period_id, unit)


# ----------------------------------------------------------------------- audit
@router.get("/audit")
def audit_log(company_id: int | None = None, entity: str | None = None, action: str | None = None,
              user_email: str | None = None, since: dt.date | None = None, limit: int = Query(200, le=2000),
              offset: int = 0, user: User = Depends(require("view")), db: Session = Depends(get_db)):
    q = select(AuditLog).where(AuditLog.tenant_id == user.tenant_id)
    if company_id:
        q = q.where(AuditLog.company_id == company_id)
    if entity:
        q = q.where(AuditLog.entity == entity)
    if action:
        q = q.where(AuditLog.action == action)
    if user_email:
        q = q.where(AuditLog.user_email.ilike(f"%{user_email}%"))
    if since:
        q = q.where(AuditLog.created_at >= dt.datetime.combine(since, dt.time.min))
    rows = db.scalars(q.order_by(AuditLog.id.desc()).offset(offset).limit(limit)).all()
    return [{**row_dict(r), "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
