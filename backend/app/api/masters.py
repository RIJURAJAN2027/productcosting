"""Masters: CRUD for every master (versioned by effective date), Excel import/export,
configuration validation and versioning."""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..audit import row_dict
from ..db import get_db
from ..deps import get_company, require
from ..engine import expr as ex
from ..models import MASTER_MODELS, Company, ReportTemplate, User
from ..services.config_builder import build_engine_config, get_template
from ..services.masters_io import export_masters, import_masters

router = APIRouter(prefix="/api/companies/{company_id}/masters", tags=["masters"])
READONLY = {"id", "company_id", "created_at", "updated_at"}


def _model(kind: str):
    if kind not in MASTER_MODELS:
        raise HTTPException(404, f"Unknown master {kind}; use one of {', '.join(MASTER_MODELS)}")
    return MASTER_MODELS[kind]


def _coerce(model, data: dict) -> dict:
    out = {}
    cols = {c.name: c for c in model.__table__.columns}
    for k, v in data.items():
        if k in READONLY or k not in cols:
            continue
        if k in ("effective_from", "effective_to") and isinstance(v, str):
            v = dt.date.fromisoformat(v[:10]) if v else None
        out[k] = v
    return out


def _validate(kind: str, data: dict) -> None:
    if kind == "gl-accounts" and data.get("gl_type") not in (None, "NONPRE", "PRE", "EXCLUDED"):
        raise HTTPException(422, "gl_type must be NONPRE, PRE or EXCLUDED")
    if kind == "categories" and data.get("sign") not in (None, 1, -1):
        raise HTTPException(422, "sign must be 1 or -1")
    if kind == "pnl-lines" and data.get("expr") is not None:
        try:
            ex.parse(data["expr"])
        except ex.ExpressionError as e:
            raise HTTPException(422, f"Formula error: {e}")
    if kind == "allocation-rules" and data.get("mode") not in (None, "plant", "pooled", "custom"):
        raise HTTPException(422, "mode must be plant, pooled or custom")


@router.get("/{kind}")
def list_master(kind: str, company: Company = Depends(get_company), db: Session = Depends(get_db),
                on: dt.date | None = None, q: str | None = None, limit: int = Query(5000, le=20000)):
    model = _model(kind)
    stmt = select(model).where(model.company_id == company.id)
    if on is not None:
        stmt = stmt.where(model.effective_from <= on, or_(model.effective_to.is_(None), model.effective_to >= on))
    rows = db.scalars(stmt.order_by(*(getattr(model, c) for c in ("sort_order", "code", "name", "id")
                                      if hasattr(model, c))).limit(limit)).all()
    out = [row_dict(r) for r in rows]
    if q:
        ql = q.lower()
        out = [r for r in out if any(ql in str(v).lower() for v in r.values() if v is not None)]
    return out


@router.post("/{kind}", status_code=201)
def create_master(kind: str, body: dict, company: Company = Depends(get_company),
                  user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    model = _model(kind)
    data = _coerce(model, body)
    _validate(kind, data)
    obj = model(company_id=company.id, **data)
    db.add(obj)
    db.flush()
    audit.log(db, user, "create", kind, obj.id, company.id, values=row_dict(obj))
    db.commit()
    return row_dict(obj)


@router.patch("/{kind}/{item_id}")
def update_master(kind: str, item_id: int, body: dict, company: Company = Depends(get_company),
                  user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    model = _model(kind)
    obj = db.get(model, item_id)
    if obj is None or obj.company_id != company.id:
        raise HTTPException(404, "Not found")
    data = _coerce(model, body)
    _validate(kind, data)
    before = row_dict(obj)
    for k, v in data.items():
        setattr(obj, k, v)
    audit.log(db, user, "update", kind, obj.id, company.id, changes=audit.diff(before, row_dict(obj)))
    db.commit()
    return row_dict(obj)


@router.delete("/{kind}/{item_id}")
def delete_master(kind: str, item_id: int, company: Company = Depends(get_company),
                  user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    model = _model(kind)
    obj = db.get(model, item_id)
    if obj is None or obj.company_id != company.id:
        raise HTTPException(404, "Not found")
    audit.log(db, user, "delete", kind, obj.id, company.id, values=row_dict(obj))
    db.delete(obj)
    db.commit()
    return {"ok": True}


@router.post("/{kind}/new-version")
def new_version(kind: str, effective_from: dt.date, company: Company = Depends(get_company),
                user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    """Close the rows valid on ``effective_from`` and copy them as a new version starting that day."""
    model = _model(kind)
    rows = db.scalars(select(model).where(model.company_id == company.id, model.effective_from < effective_from,
                                          or_(model.effective_to.is_(None),
                                              model.effective_to >= effective_from))).all()
    for r in rows:
        data = {c.name: getattr(r, c.name) for c in model.__table__.columns if c.name not in READONLY}
        data.update(effective_from=effective_from, effective_to=None)
        r.effective_to = effective_from - dt.timedelta(days=1)
        db.add(model(company_id=company.id, **data))
    audit.log(db, user, "new_version", kind, None, company.id, effective_from=effective_from, rows=len(rows))
    db.commit()
    return {"copied": len(rows)}


@router.get("-export")
def export_xlsx(company: Company = Depends(get_company), db: Session = Depends(get_db)):
    data = export_masters(db, company)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="masters_{company.code}.xlsx"'})


@router.post("-import")
async def import_xlsx(file: UploadFile = File(...), mode: str = Form("merge"), company: Company = Depends(get_company),
                      user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    return import_masters(db, company, await file.read(), user, mode)


@router.get("-validate")
def validate(company: Company = Depends(get_company), db: Session = Depends(get_db), on: dt.date | None = None):
    cfg = build_engine_config(db, company, on)
    problems = cfg.validate()
    return {"problems": problems, "counts": {"plants": len(cfg.plants), "product_groups": len(cfg.product_groups),
                                              "business_areas": len(cfg.business_areas),
                                              "gl_accounts": len(cfg.gl_accounts),
                                              "cost_centres": len(cfg.cost_centres),
                                              "categories": len(cfg.categories), "rules": len(cfg.rules),
                                              "lines": len(cfg.lines)}}


@router.get("-templates/{name}")
def get_report_template(name: str, company: Company = Depends(get_company), db: Session = Depends(get_db)):
    return get_template(db, company, name, build_engine_config(db, company))


@router.put("-templates/{name}")
def put_report_template(name: str, rows: list[dict[str, Any]], company: Company = Depends(get_company),
                        user: User = Depends(require("edit")), db: Session = Depends(get_db)):
    t = db.scalar(select(ReportTemplate).where(ReportTemplate.company_id == company.id, ReportTemplate.name == name))
    if t is None:
        t = ReportTemplate(company_id=company.id, name=name, rows=rows)
        db.add(t)
    else:
        t.rows = rows
    audit.log(db, user, "update", "report_template", name, company.id, rows=len(rows))
    db.commit()
    return {"ok": True, "rows": len(rows)}
