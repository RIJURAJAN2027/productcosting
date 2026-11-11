"""Calculation runs: gather inputs, run the engine, store results, approval workflow."""
from __future__ import annotations

import datetime as dt
import time
from collections import OrderedDict
from threading import Lock

import numpy as np
import pandas as pd
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..engine import CostingEngine, EngineConfig, EngineInputs, EngineResult
from ..models import (AdjustmentRule, CalcRun, Company, DriverValue, McAdjustment, Period, RunValue, TBLine, Upload,
                      User, now)
from .config_builder import build_engine_config


def active_upload(db: Session, period: Period, kind: str) -> Upload | None:
    return db.scalar(select(Upload).where(Upload.period_id == period.id, Upload.kind == kind,
                                          Upload.is_active.is_(True)).order_by(Upload.id.desc()))


def tb_frame(db: Session, upload_id: int | None, dim: str) -> pd.DataFrame:
    if upload_id is None:
        return pd.DataFrame(columns=["gl", dim, "amount", "gl_desc"])
    rows = db.execute(select(TBLine.gl, TBLine.dim, TBLine.amount, TBLine.gl_desc)
                      .where(TBLine.upload_id == upload_id)).all()
    return pd.DataFrame(rows, columns=["gl", dim, "amount", "gl_desc"])


def adjustment_dicts(rules: list[AdjustmentRule]) -> list[dict]:
    out = []
    for r in rules:
        d = dict(r.definition or {})
        d.update({"type": r.type, "reason": r.reason, "author": r.author_name, "enabled": r.enabled, "id": r.id})
        out.append(d)
    return out


def gather_inputs(db: Session, company: Company, period: Period) -> tuple[EngineInputs, dict]:
    ba = active_upload(db, period, "BA_TB")
    cc = active_upload(db, period, "CC_TB")
    if ba is None:
        raise HTTPException(409, "Upload a business-area TB for this period before calculating.")
    rules = db.scalars(select(AdjustmentRule).where(
        AdjustmentRule.company_id == company.id,
        or_(AdjustmentRule.period_id == period.id, AdjustmentRule.period_id.is_(None)))
        .order_by(AdjustmentRule.sort_order, AdjustmentRule.id)).all()
    adjustments = adjustment_dicts(rules)
    mc = {}
    div = float((company.settings or {}).get("calc_unit_divisor", 1e7))
    for m in db.scalars(select(McAdjustment).where(McAdjustment.period_id == period.id)).all():
        key = f"{m.plant_code}|{m.pg_code}"
        # amounts are stored in rupees; the engine works in calculation units
        mc[key] = m.value / div if m.kind == "amount" else {"kind": "mc_pct", "value": m.value}
    drivers: dict[str, dict] = {}
    for d in db.scalars(select(DriverValue).where(DriverValue.company_id == company.id,
                                                  or_(DriverValue.period_id == period.id,
                                                      DriverValue.period_id.is_(None)))).all():
        drivers.setdefault(d.driver, {})[f"{d.plant_code}|{d.pg_code}"] = d.value
    snap = {"ba_upload_id": ba.id, "cc_upload_id": cc.id if cc else None, "adjustments": adjustments,
            "mc_adjustments": mc, "drivers": drivers, "period_end": period.end_date.isoformat()}
    return inputs_from_snapshot(db, snap), snap


def inputs_from_snapshot(db: Session, snap: dict) -> EngineInputs:
    return EngineInputs(ba_tb=tb_frame(db, snap.get("ba_upload_id"), "ba"),
                        cc_tb=tb_frame(db, snap.get("cc_upload_id"), "cc"),
                        adjustments=snap.get("adjustments") or [], drivers=snap.get("drivers") or {},
                        mc_adjustments=snap.get("mc_adjustments") or {})


def store_values(db: Session, run: CalcRun, result: EngineResult) -> int:
    div = result.engine.div
    table = result.long_table()
    rows = [{"run_id": run.id, "line": r.line, "plant": r.plant, "code": r.code, "value": float(r.value) * div}
            for r in table.itertuples(index=False)]
    # COR memo per plant (corporate costs before pooling) for the PG-wise "COR" column
    for code in result.lines:
        memo = result.cor_memo(code)
        for i, p in enumerate(result.plants):
            if memo[i]:
                rows.append({"run_id": run.id, "line": code, "plant": p, "code": "COR", "value": float(memo[i]) * div})
    db.execute(RunValue.__table__.insert(), rows)
    return len(rows)


def execute_run(db: Session, company: Company, period: Period, user: User | None, note: str | None = None) -> CalcRun:
    t0 = time.perf_counter()
    cfg = build_engine_config(db, company, period.end_date)
    problems = [p for p in cfg.validate() if p["level"] == "error"]
    if problems:
        raise HTTPException(422, {"message": "The configuration has errors", "problems": problems})
    inputs, snap = gather_inputs(db, company, period)
    run = CalcRun(company_id=company.id, period_id=period.id, status="draft", started_by=user.id if user else None,
                  config_snapshot=cfg.to_dict(), inputs_snapshot=snap, note=note)
    db.add(run)
    db.flush()
    try:
        result = CostingEngine(cfg, inputs).run()
    except Exception as e:  # engine errors are stored on the run for the user to see
        run.status, run.error = "failed", f"{type(e).__name__}: {e}"
        run.finished_at = now()
        audit.log(db, user, "run_failed", "run", run.id, company.id, period=period.code, error=run.error)
        db.commit()
        raise HTTPException(422, f"Calculation failed: {e}")
    run.controls = [c.as_dict() for c in result.controls]
    run.control_summary = result.control_summary()
    n = store_values(db, run, result)
    run.timings = {**{k: round(v, 4) for k, v in result.timings.items()}, "stored_values": n}
    run.finished_at = now()
    run.duration_s = round(time.perf_counter() - t0, 3)
    _CACHE.put(run.id, result)
    audit.log(db, user, "run_calculated", "run", run.id, company.id, period=period.code,
              controls=run.control_summary, duration_s=run.duration_s)
    db.commit()
    return run


class _ResultCache:
    """Small LRU of finished engine results (for drill-down without re-reading everything)."""

    def __init__(self, size: int = 8):
        self.size, self.data, self.lock = size, OrderedDict(), Lock()

    def get(self, key):
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        return None

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)


_CACHE = _ResultCache()


def load_result(db: Session, run: CalcRun) -> EngineResult:
    """Re-create the engine result of a run from its snapshots (deterministic)."""
    cached = _CACHE.get(run.id)
    if cached is not None:
        return cached
    cfg = EngineConfig.from_dict(run.config_snapshot)
    result = CostingEngine(cfg, inputs_from_snapshot(db, run.inputs_snapshot)).run()
    _CACHE.put(run.id, result)
    return result


# ------------------------------------------------------------------ workflow (maker-checker)
TRANSITIONS = {
    "submit": ({"draft", "rejected"}, "submitted"),
    "approve": ({"submitted"}, "approved"),
    "reject": ({"submitted", "approved"}, "rejected"),
    "publish": ({"approved"}, "published"),
}


def transition(db: Session, run: CalcRun, action: str, user: User, comment: str | None = None) -> CalcRun:
    if action not in TRANSITIONS:
        raise HTTPException(400, f"Unknown action {action}")
    allowed, target = TRANSITIONS[action]
    if run.status not in allowed:
        raise HTTPException(409, f"Cannot {action} a run that is {run.status}.")
    allow_self = bool((getattr(user, "_tenant_settings", None) or {}).get("allow_self_approval"))
    if action in ("approve", "publish", "reject") and run.submitted_by == user.id and not allow_self:
        raise HTTPException(403, "Maker-checker: the person who submitted a run cannot approve, reject or "
                                 "publish it. Ask another approver.")
    if action == "submit" and run.control_summary.get("errors") and not comment:
        raise HTTPException(409, "This run has control errors; add a comment explaining them to submit it.")
    run.status = target
    if action == "submit":
        run.submitted_by, run.submitted_at = user.id, now()
    if action in ("approve", "reject"):
        run.approved_by, run.approved_at = user.id, now()
    if comment:
        run.review_comment = comment
    if action == "publish":
        for other in db.scalars(select(CalcRun).where(CalcRun.period_id == run.period_id, CalcRun.id != run.id,
                                                      CalcRun.status == "published")).all():
            other.status = "superseded"
    audit.log(db, user, f"run_{action}", "run", run.id, run.company_id, status=target, comment=comment)
    db.commit()
    return run


def latest_run(db: Session, period_id: int, published_only: bool = False) -> CalcRun | None:
    q = select(CalcRun).where(CalcRun.period_id == period_id, CalcRun.status != "failed")
    if published_only:
        q = q.where(CalcRun.status == "published")
    run = db.scalar(q.order_by(CalcRun.id.desc()))
    if run is None and published_only:
        return None
    if not published_only:
        pub = db.scalar(select(CalcRun).where(CalcRun.period_id == period_id, CalcRun.status == "published")
                        .order_by(CalcRun.id.desc()))
        return pub or run
    return run


def values_frame(db: Session, run_id: int) -> pd.DataFrame:
    rows = db.execute(select(RunValue.line, RunValue.plant, RunValue.code, RunValue.value)
                      .where(RunValue.run_id == run_id)).all()
    return pd.DataFrame(rows, columns=["line", "plant", "code", "value"])


def value_lookup(db: Session, run_id: int) -> dict[tuple[str, str, str], float]:
    return {(r.line, r.plant, r.code): r.value for r in db.execute(
        select(RunValue.line, RunValue.plant, RunValue.code, RunValue.value).where(RunValue.run_id == run_id)).all()}


def period_months(period: Period) -> int:
    if period.months:
        return period.months
    if period.start_date:
        return max(1, (period.end_date.year - period.start_date.year) * 12 + period.end_date.month
                   - period.start_date.month + 1)
    return 1


def to_float(x) -> float:
    try:
        f = float(x)
        return f if np.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def today() -> dt.date:
    return dt.date.today()
