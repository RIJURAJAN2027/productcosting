"""TB / MIS uploads: parse, validate against masters, store, derive monthly figures."""
from __future__ import annotations

import hashlib
import io

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import audit
from ..engine.config import code_str, norm
from ..importers import tb as tbmod
from ..models import (BusinessArea, Company, CostCentre, GLAccount, MisLine, Period, TBLine, Upload, User)
from .config_builder import active, company_settings

KINDS = {"BA_TB": "BA", "CC_TB": "CC"}


def known_codes(db: Session, company: Company, period: Period, kind: str) -> tuple[set[str], set[str], dict]:
    gls = db.scalars(active(GLAccount, company.id, period.end_date)).all()
    if kind == "BA_TB":
        dims = {b.code for b in db.scalars(active(BusinessArea, company.id, period.end_date)).all()}
    else:
        dims = {c.code for c in db.scalars(active(CostCentre, company.id, period.end_date)).all()}
    return {g.code for g in gls}, dims, {g.code: g for g in gls}


def store_tb(db: Session, company: Company, period: Period, kind: str, filename: str, data: bytes | None,
             long: pd.DataFrame, summary: dict, issues: list[dict], user: User | None) -> Upload:
    errors = any(i.get("level") == "error" for i in issues)
    warnings = any(i.get("level") == "warning" for i in issues)
    db.execute(update(Upload).where(Upload.period_id == period.id, Upload.kind == kind).values(is_active=False))
    up = Upload(company_id=company.id, period_id=period.id, kind=kind, filename=filename,
                sha256=hashlib.sha256(data or long.to_csv().encode()).hexdigest(),
                uploaded_by=user.id if user else None,
                status="errors" if errors else ("warnings" if warnings else "validated"),
                summary=summary, issues=issues[:2000], is_active=True)
    db.add(up)
    db.flush()
    dim = "dim" if "dim" in long.columns else ("ba" if "ba" in long.columns else "cc")
    rows = [{"upload_id": up.id, "gl": code_str(r.gl), "gl_desc": str(getattr(r, "gl_desc", "") or "")[:300],
             "dim": code_str(getattr(r, dim)), "amount": float(r.amount)} for r in long.itertuples(index=False)
            if float(r.amount) != 0.0]
    if rows:
        db.execute(TBLine.__table__.insert(), rows)
    audit.log(db, user, "upload", "upload", up.id, company.id, kind=kind, filename=filename, period=period.code,
              status=up.status, rows=len(rows), total=summary.get("total"))
    return up


def upload_tb(db: Session, company: Company, period: Period, kind: str, filename: str, data: bytes,
              user: User | None) -> Upload:
    if kind not in KINDS:
        raise HTTPException(400, f"Unknown TB kind {kind}; use BA_TB or CC_TB")
    s = company_settings(company)
    try:
        parsed = tbmod.read_tb_file(data, filename, kind=KINDS[kind], ignore_columns=s.get("ignore_columns", []))
    except ValueError as e:
        raise HTTPException(422, str(e))
    gls, dims, glmap = known_codes(db, company, period, kind)
    issues = parsed.issues + tbmod.validate_against_masters(parsed, gls, dims)
    summary = parsed.summary()
    df = parsed.long
    # totals by GL type help the user see what will be used
    types = df["gl"].map(lambda g: glmap[g].gl_type if g in glmap else "UNMAPPED")
    summary["total_by_gl_type"] = {k: float(v) for k, v in df.groupby(types)["amount"].sum().items()}
    if kind == "CC_TB":
        summary["pre_total"] = float(df.loc[types == "PRE", "amount"].sum())
    up = store_tb(db, company, period, kind, filename, data, df, summary, issues, user)
    db.commit()
    return up


def upload_mis(db: Session, company: Company, period: Period, filename: str, data: bytes, user: User | None) -> dict:
    """MIS master: an Excel/CSV with columns 'Particulars', 'MIS' and optionally 'Formula' (P&L expression)
    and 'Compare' (sum|difference).  Rows without a formula reuse the formula of an existing line with the
    same label."""
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data))
    else:
        df = pd.read_excel(io.BytesIO(data))
    cols = {norm(c): c for c in df.columns}
    lab = cols.get("particulars") or cols.get("label") or list(df.columns)[0]
    val = cols.get("mis") or cols.get("mis value") or cols.get("value")
    if val is None:
        raise HTTPException(422, "The MIS file needs a 'MIS' column")
    fcol, ccol = cols.get("formula"), cols.get("compare")
    existing = {norm(m.label): m for m in db.scalars(select(MisLine).where(MisLine.period_id == period.id)).all()}
    s = company_settings(company)
    count = 0
    for i, r in df.iterrows():
        label = str(r[lab]).strip()
        if not label or label.lower() == "nan" or pd.isna(r[val]):
            continue
        m = existing.get(norm(label))
        expr = str(r[fcol]) if fcol and not pd.isna(r[fcol]) else (m.expr if m else None)
        if not expr:
            continue
        if m is None:
            m = MisLine(company_id=company.id, period_id=period.id, label=label, expr=expr, sort_order=i)
            db.add(m)
        m.mis_value = float(r[val]) * float(s.get("mis_upload_multiplier", 1.0))
        m.expr = expr
        if ccol and not pd.isna(r[ccol]):
            m.compare = str(r[ccol]).strip().lower()
        count += 1
    audit.log(db, user, "upload", "mis", period.id, company.id, filename=filename, rows=count)
    db.commit()
    return {"rows": count}


def derive_monthly(db: Session, company: Company, month: Period, ytd_current: Period, ytd_previous: Period | None,
                   user: User | None) -> list[int]:
    """Monthly TB = YTD(current) - YTD(previous) for both BA and CC TBs."""
    from .runs import active_upload, tb_frame
    created = []
    for kind, dim in (("BA_TB", "ba"), ("CC_TB", "cc")):
        cur = active_upload(db, ytd_current, kind)
        if cur is None:
            continue
        a = tb_frame(db, cur.id, dim).groupby(["gl", dim], as_index=False).agg(amount=("amount", "sum"),
                                                                               gl_desc=("gl_desc", "first"))
        if ytd_previous is not None and (prev := active_upload(db, ytd_previous, kind)) is not None:
            b = tb_frame(db, prev.id, dim).groupby(["gl", dim], as_index=False)["amount"].sum()
            m = a.merge(b, on=["gl", dim], how="outer", suffixes=("", "_prev")).fillna({"amount": 0.0,
                                                                                        "amount_prev": 0.0})
            m["amount"] = m["amount"] - m["amount_prev"]
            m["gl_desc"] = m["gl_desc"].fillna("")
            a = m
        a = a.rename(columns={dim: "dim"})
        summary = {"derived_from": [ytd_current.code, ytd_previous.code if ytd_previous else None],
                   "total": float(a["amount"].sum()), "gl_rows": int(a["gl"].nunique())}
        up = store_tb(db, company, month, kind, f"derived {ytd_current.code} - "
                      f"{ytd_previous.code if ytd_previous else '0'}", None, a, summary, [], user)
        created.append(up.id)
    db.commit()
    return created
