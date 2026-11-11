"""Create a company configuration (and optionally a period) from a legacy Excel workbook."""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..engine import CostingEngine, EngineConfig, EngineInputs
from ..importers.legacy_reference import compare_with_workbook
from ..importers.legacy_workbook import LegacyWorkbookImporter
from ..models import AdjustmentRule, Company, McAdjustment, MisLine, Period, Upload, User
from .config_builder import save_engine_config, save_templates
from .uploads import store_tb


def import_legacy(db: Session, company: Company, data: bytes, filename: str, user: User | None,
                  period_code: str | None = None, period_label: str | None = None,
                  end_date: dt.date | None = None, start_date: dt.date | None = None,
                  verify: bool = True) -> dict:
    imp = LegacyWorkbookImporter(data)
    res = imp.run()
    cfg = res.config
    effective_from = dt.date(2000, 1, 1)
    extra = {"families": res.masters["families"], "business_areas": res.masters["business_areas"],
             "gl_accounts": res.masters["gl_accounts"], "cost_centres": res.masters["cost_centres"]}
    counts = save_engine_config(db, company, cfg, effective_from, extra)
    save_templates(db, company, res.templates)
    out: dict = {"counts": counts, "notes": res.notes, "note_counts": res.note_counts(), "period_id": None,
                 "run_id": None, "regression": None}
    if period_code:
        end = end_date or dt.date.today()
        period = db.scalar(select(Period).where(Period.company_id == company.id, Period.code == period_code,
                                                Period.scenario == "actual"))
        if period is None:
            months = 1
            if start_date:
                months = (end.year - start_date.year) * 12 + end.month - start_date.month + 1
            period = Period(company_id=company.id, code=period_code, label=period_label or period_code,
                            end_date=end, start_date=start_date, months=months, basis="YTD")
            db.add(period)
            db.flush()
        div = float(cfg["calc_unit_divisor"])
        for kind, frame, dim in (("BA_TB", res.ba_tb, "ba"), ("CC_TB", res.cc_tb, "cc")):
            long = frame.rename(columns={dim: "dim"})
            store_tb(db, company, period, kind, f"{filename} ({kind})", None, long,
                     {"source": "legacy workbook", "total": float(long["amount"].sum()),
                      "gl_rows": int(long["gl"].nunique())}, [], user)
        db.query(AdjustmentRule).filter(AdjustmentRule.period_id == period.id).delete()
        for i, a in enumerate(res.adjustments):
            definition = {k: v for k, v in a.items() if k not in ("type", "reason", "author")}
            db.add(AdjustmentRule(company_id=company.id, period_id=period.id, type=a["type"], definition=definition,
                                  reason=a.get("reason", ""), author_name=a.get("author", "legacy import"),
                                  author_id=user.id if user else None, sort_order=i))
        db.query(McAdjustment).filter(McAdjustment.period_id == period.id).delete()
        for key, value in res.mc_adjustments.items():
            p, g = key.split("|", 1)
            db.add(McAdjustment(company_id=company.id, period_id=period.id, plant_code=p, pg_code=g, kind="amount",
                                value=float(value) * div, reason="Worked-out MC from legacy workbook"))
        db.query(MisLine).filter(MisLine.period_id == period.id).delete()
        for i, m in enumerate(res.mis):
            db.add(MisLine(company_id=company.id, period_id=period.id, label=m["label"], expr=m["expr"],
                           mis_value=float(m["mis_value"]) * div, compare=m["compare"], sort_order=i))
        db.flush()
        out["period_id"] = period.id
        if verify:
            engine_cfg = EngineConfig.from_dict(cfg)
            result = CostingEngine(engine_cfg, EngineInputs(res.ba_tb, res.cc_tb, res.adjustments, {},
                                                            res.mc_adjustments)).run()
            cmp = compare_with_workbook(imp, result)
            summary = cmp.summary()
            out["regression"] = {
                "tolerance_rupees": 1.0, "cells_compared": int(len(cmp.table)),
                "mismatches": int(len(cmp.mismatches)),
                "by_section": summary.to_dict(orient="records"),
                "worst": cmp.mismatches.sort_values("abs_diff", ascending=False).head(25).to_dict(orient="records"),
            }
    audit.log(db, user, "legacy_import", "company", company.id, company.id, filename=filename,
              counts=counts, notes=out["note_counts"], regression=(out["regression"] or {}).get("mismatches"))
    db.commit()
    return out
