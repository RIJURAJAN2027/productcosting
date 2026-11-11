"""Company backup / restore as a compressed JSON document.

The backup contains settings, every master (all versions), report templates,
periods, uploaded TB lines, adjustments, MC adjustments, drivers, MIS lines and
the calculation runs with their stored results – everything needed to restore
the company on another installation.  (Database-level backups: see the admin guide.)
"""
from __future__ import annotations

import datetime as dt
import gzip
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..audit import row_dict
from ..models import (AdjustmentRule, AllocationRule, BusinessArea, CalcRun, Category, Company, CostCentre,
                      DriverValue, GLAccount, McAdjustment, MisLine, Period, PnlLine, Plant, PreFunction,
                      ProductGroup, ReportTemplate, RunValue, TBLine, Tenant, Upload, User)

FORMAT = "product-costing-backup/1"
MASTERS = [Plant, ProductGroup, BusinessArea, GLAccount, CostCentre, Category, PreFunction, AllocationRule, PnlLine,
           ReportTemplate]
PERIOD_CHILDREN = [AdjustmentRule, McAdjustment, DriverValue, MisLine]


def _rows(db: Session, model, **filters) -> list[dict]:
    q = select(model)
    for k, v in filters.items():
        q = q.where(getattr(model, k) == v)
    return [row_dict(o) for o in db.scalars(q.order_by(model.id)).all()]


def export_company(db: Session, company: Company, include_runs: bool = True) -> bytes:
    doc = {"format": FORMAT, "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "company": row_dict(company), "masters": {}, "periods": [], "standing": {}}
    for model in MASTERS:
        doc["masters"][model.__tablename__] = _rows(db, model, company_id=company.id)
    for model in PERIOD_CHILDREN:
        doc["standing"][model.__tablename__] = [r for r in _rows(db, model, company_id=company.id)
                                                if r.get("period_id") is None]
    for p in db.scalars(select(Period).where(Period.company_id == company.id).order_by(Period.id)).all():
        entry = {"period": row_dict(p), "uploads": [], "children": {}, "runs": []}
        for up in db.scalars(select(Upload).where(Upload.period_id == p.id).order_by(Upload.id)).all():
            lines = db.execute(select(TBLine.gl, TBLine.gl_desc, TBLine.dim, TBLine.amount)
                               .where(TBLine.upload_id == up.id)).all()
            entry["uploads"].append({"upload": row_dict(up), "lines": [list(r) for r in lines]})
        for model in PERIOD_CHILDREN:
            entry["children"][model.__tablename__] = _rows(db, model, period_id=p.id)
        if include_runs:
            for run in db.scalars(select(CalcRun).where(CalcRun.period_id == p.id).order_by(CalcRun.id)).all():
                vals = db.execute(select(RunValue.line, RunValue.plant, RunValue.code, RunValue.value)
                                  .where(RunValue.run_id == run.id)).all()
                entry["runs"].append({"run": row_dict(run), "values": [list(v) for v in vals]})
        doc["periods"].append(entry)
    return gzip.compress(json.dumps(doc, default=str).encode())


def _clean(d: dict, drop=("id", "company_id", "period_id", "upload_id", "run_id")) -> dict:
    out = {k: v for k, v in d.items() if k not in drop}
    for k, v in list(out.items()):
        if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-" and k in (
                "effective_from", "effective_to", "end_date", "start_date", "license_expires"):
            out[k] = dt.date.fromisoformat(v)
        elif isinstance(v, str) and k.endswith("_at") and "T" in v:
            out[k] = dt.datetime.fromisoformat(v)
    return out


def restore_company(db: Session, tenant: Tenant, data: bytes, user: User | None, new_code: str | None = None) -> Company:
    try:
        doc = json.loads(gzip.decompress(data))
    except Exception:
        raise HTTPException(422, "Not a valid backup file")
    if doc.get("format") != FORMAT:
        raise HTTPException(422, "Unsupported backup format")
    c = _clean(doc["company"], drop=("id", "tenant_id"))
    c["code"] = new_code or c["code"]
    if db.scalar(select(Company).where(Company.tenant_id == tenant.id, Company.code == c["code"])):
        raise HTTPException(409, f"Company code {c['code']} already exists; choose another code")
    company = Company(tenant_id=tenant.id, **c)
    db.add(company)
    db.flush()
    by_table = {m.__tablename__: m for m in MASTERS + PERIOD_CHILDREN}
    for table, rows in doc["masters"].items():
        for r in rows:
            db.add(by_table[table](company_id=company.id, **_clean(r)))
    for table, rows in doc.get("standing", {}).items():
        for r in rows:
            db.add(by_table[table](company_id=company.id, period_id=None, **_clean(r)))
    for entry in doc["periods"]:
        p = Period(company_id=company.id, **_clean(entry["period"], drop=("id", "company_id", "locked_by")))
        db.add(p)
        db.flush()
        upload_map = {}
        for u in entry["uploads"]:
            old = u["upload"]
            up = Upload(company_id=company.id, period_id=p.id,
                        **_clean(old, drop=("id", "company_id", "period_id", "uploaded_by")))
            db.add(up)
            db.flush()
            upload_map[old["id"]] = up.id
            if u["lines"]:
                db.execute(TBLine.__table__.insert(), [{"upload_id": up.id, "gl": g, "gl_desc": d, "dim": dim,
                                                        "amount": a} for g, d, dim, a in u["lines"]])
        for table, rows in entry["children"].items():
            for r in rows:
                db.add(by_table[table](company_id=company.id, period_id=p.id,
                                       **_clean(r, drop=("id", "company_id", "period_id", "author_id"))))
        for rr in entry["runs"]:
            run = rr["run"]
            snap = dict(run.get("inputs_snapshot") or {})
            for k in ("ba_upload_id", "cc_upload_id"):
                if snap.get(k) in upload_map:
                    snap[k] = upload_map[snap[k]]
            new = CalcRun(company_id=company.id, period_id=p.id,
                          **_clean({**run, "inputs_snapshot": snap},
                                   drop=("id", "company_id", "period_id", "started_by", "submitted_by",
                                         "approved_by")))
            db.add(new)
            db.flush()
            if rr["values"]:
                db.execute(RunValue.__table__.insert(), [{"run_id": new.id, "line": a, "plant": b, "code": cc,
                                                          "value": v} for a, b, cc, v in rr["values"]])
    audit.log(db, user, "restore", "company", company.id, company.id, source=doc["company"].get("code"))
    db.commit()
    return company
