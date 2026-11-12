"""Report builders.  All reports read the stored results of a run (values in rupees)
and present them in the chosen unit; drill-down re-creates the engine result."""
from __future__ import annotations

from typing import Any

import numpy as np
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import EngineConfig, expr as ex
from ..engine.config import norm
from ..models import CalcRun, Company, McAdjustment, MisLine, Period, ProductGroup
from .config_builder import UNITS, company_settings, get_template
from .runs import latest_run, load_result, period_months, value_lookup

ALL, TOTAL = "__ALL__", "__TOTAL__"
REPORTS = {
    "pg_wise": "PG-wise P&L",
    "plant_wise": "Plant-wise P&L",
    "pg_totals": "Product-group totals across plants",
    "all_pg": "All product groups (company)",
}
KEY_LINES = [
    ("sales", ["sales"]), ("material_cost", ["material cost"]), ("variable_cost", ["variable cost"]),
    ("total_variable_cost", ["total variable cost"]), ("gross_margin", ["gross margin"]),
    ("factory_overhead", ["factory overhead"]), ("pre_factory", ["pre-factory related"]),
    ("plant_profit", ["plant profit"]), ("corporate_overheads", ["corporate overheads"]),
    ("pre_corporate", ["pre-corporate"]), ("other_credits", ["other credits/ (debits)", "other credits"]),
    ("pbt", ["pbt"]), ("bep", ["bep"]),
]


def unit_divisor(company: Company, unit: str | None) -> tuple[float, str]:
    s = company_settings(company)
    u = unit or s["display_unit"]
    if u not in UNITS:
        raise HTTPException(400, f"Unknown unit {u}; use one of {', '.join(UNITS)}")
    return UNITS[u], u


def run_context(db: Session, run: CalcRun) -> tuple[Company, Period, EngineConfig]:
    company = db.get(Company, run.company_id)
    period = db.get(Period, run.period_id)
    cfg = EngineConfig.from_dict(run.config_snapshot)
    return company, period, cfg


def key_line_codes(cfg: EngineConfig, template: list[dict] | None = None) -> dict[str, str]:
    """Resolve the headline lines (sales, PBT ...) by meta settings or by label."""
    out: dict[str, str] = {}
    labels: dict[str, str] = {}
    for row in (template or []):
        if row.get("line"):
            labels.setdefault(norm(row["label"]), row["line"])
    for ln in cfg.lines:
        if not ln.get("hidden"):
            labels.setdefault(norm(ln["label"]), ln["code"])
    meta = cfg.meta or {}
    for key, candidates in KEY_LINES:
        if meta.get(f"{key}_line"):
            out[key] = meta[f"{key}_line"]
            continue
        for c in candidates:
            if c in labels:
                out[key] = labels[c]
                break
    if "sales" not in out and meta.get("sales_line"):
        out["sales"] = meta["sales_line"]
    return out


def _families(db: Session, company: Company) -> dict[str, str]:
    return {p.code: p.family or "" for p in db.scalars(select(ProductGroup).where(ProductGroup.company_id ==
                                                                                 company.id)).all()}


def report_columns(name: str, cfg: EngineConfig, plant: str | None, pg: str | None, families: dict) -> list[dict]:
    if name == "pg_wise":
        plant = plant or cfg.plants[0]
        if plant not in cfg.plants:
            raise HTTPException(404, f"Unknown plant {plant}")
        cols = [{"plant": plant, "code": g, "label": g, "family": families.get(g, "")} for g in cfg.product_groups]
        cols.append({"plant": plant, "code": cfg.com_code, "label": cfg.com_code, "family": "Common"})
        cols.append({"plant": plant, "code": "COR", "label": "COR", "family": "Corp (memo)", "memo": True})
        cols.append({"plant": plant, "code": TOTAL, "label": f"{plant} total", "family": "Total"})
        return cols
    if name == "plant_wise":
        return [{"plant": p, "code": TOTAL, "label": p, "family": "Total"} for p in cfg.plants] + \
            [{"plant": ALL, "code": TOTAL, "label": "Company", "family": "Total"}]
    if name == "pg_totals":
        pg = pg or cfg.product_groups[0]
        if pg not in cfg.product_groups + [cfg.com_code]:
            raise HTTPException(404, f"Unknown product group {pg}")
        return [{"plant": p, "code": pg, "label": p, "family": families.get(pg, "")} for p in cfg.plants] + \
            [{"plant": ALL, "code": pg, "label": "Total", "family": families.get(pg, "")}]
    if name == "all_pg":
        cols = [{"plant": ALL, "code": g, "label": g, "family": families.get(g, "")} for g in cfg.product_groups]
        cols.append({"plant": ALL, "code": cfg.com_code, "label": cfg.com_code, "family": "Common"})
        cols.append({"plant": ALL, "code": TOTAL, "label": "Total", "family": "Total"})
        return cols
    raise HTTPException(404, f"Unknown report {name}")


def build_report(db: Session, run: CalcRun, name: str, plant: str | None = None, pg: str | None = None,
                 unit: str | None = None) -> dict:
    company, period, cfg = run_context(db, run)
    div, unit_name = unit_divisor(company, unit)
    template = get_template(db, company, "plant_wise" if name == "plant_wise" else "pg_wise", cfg)
    cols = report_columns(name, cfg, plant, pg, _families(db, company))
    vals = value_lookup(db, run.id)
    lines = {ln["code"]: ln for ln in cfg.lines}

    def value(line: str, col: dict) -> float:
        return vals.get((line, col["plant"], col["code"]), 0.0)

    out_rows = []
    for row in template:
        kind = row.get("kind", "line")
        if kind != "line" or not row.get("line"):
            out_rows.append({"kind": kind, "label": row.get("label", ""), "style": row.get("style") or {}})
            continue
        code = row["line"]
        values = [value(code, c) / div for c in cols]
        pct = []
        base_text = row.get("pct_base") or lines.get(code, {}).get("pct_base")
        for c, v in zip(cols, values):
            if not base_text or c.get("memo"):
                pct.append(None)
                continue
            try:
                base = ex.evaluate(base_text, lambda r, c=c: value(r.body.partition("@")[0], c) / div
                                   if r.kind == "L" else 0.0)
            except ex.ExpressionError:
                base = 0.0
            pct.append(float(v / base) if base else None)
        out_rows.append({"kind": "line", "label": row.get("label") or lines.get(code, {}).get("label", code),
                         "line": code, "style": row.get("style") or {}, "values": values, "pct": pct,
                         "ratio": lines.get(code, {}).get("additive") is False})
    title = REPORTS.get(name, name)
    if name == "pg_wise":
        title = f"{title} – {cols[0]['plant']}"
    if name == "pg_totals":
        title = f"{title} – {cols[0]['code']}"
    return {"report": name, "title": title, "unit": unit_name, "company": company.name, "currency": company.currency,
            "period": {"code": period.code, "label": period.label, "scenario": period.scenario},
            "run": {"id": run.id, "status": run.status}, "columns": cols, "rows": out_rows}


# ----------------------------------------------------------------------- MC %
def mc_report(db: Session, run: CalcRun, unit: str | None = None) -> dict:
    company, period, cfg = run_context(db, run)
    div, unit_name = unit_divisor(company, unit)
    result = load_result(db, run)
    e = result.engine
    keys = key_line_codes(cfg, get_template(db, company, "pg_wise", cfg))
    sales_code, mc_code = keys.get("sales"), keys.get("material_cost")
    if not sales_code or not mc_code:
        raise HTTPException(422, "Set meta.sales_line and meta.material_cost_line in company settings.")
    mcadj = e.mc_adjustment_grid()
    rows = []
    cdiv = e.div / div
    for p in cfg.plants:
        for g in cfg.product_groups + [cfg.com_code]:
            i, j = e.pidx[p], e.kidx[g]
            sales = float(e.line(sales_code)[i, j]) * cdiv
            mc = float(e.line(mc_code)[i, j]) * cdiv
            adj = float(mcadj[i, j]) * cdiv
            if not any(abs(x) > 1e-12 for x in (sales, mc, adj)):
                continue
            rows.append({"plant": p, "pg": g, "sales": sales, "mc_tb": mc - adj, "mc_adjustment": adj,
                         "mc_worked_out": mc, "mc_pct_tb": (mc - adj) / sales if sales else None,
                         "mc_pct": mc / sales if sales else None})
        ps = float(e.aggregate_value(sales_code, p, None)) * cdiv
        pm = float(e.aggregate_value(mc_code, p, None)) * cdiv
        pa = float(mcadj[e.pidx[p], :].sum()) * cdiv
        rows.append({"plant": p, "pg": TOTAL, "sales": ps, "mc_tb": pm - pa, "mc_adjustment": pa, "mc_worked_out": pm,
                     "mc_pct_tb": (pm - pa) / ps if ps else None, "mc_pct": pm / ps if ps else None})
    ts, tm, ta = (float(e.aggregate_value(sales_code, None, None)) * cdiv,
                  float(e.aggregate_value(mc_code, None, None)) * cdiv, float(mcadj.sum()) * cdiv)
    rows.append({"plant": ALL, "pg": TOTAL, "sales": ts, "mc_tb": tm - ta, "mc_adjustment": ta, "mc_worked_out": tm,
                 "mc_pct_tb": (tm - ta) / ts if ts else None, "mc_pct": tm / ts if ts else None})
    entries = [{"plant": m.plant_code, "pg": m.pg_code, "kind": m.kind, "value": m.value, "reason": m.reason}
               for m in db.scalars(select(McAdjustment).where(McAdjustment.period_id == period.id)).all()]
    return {"report": "mc", "title": "Material cost %", "unit": unit_name, "company": company.name,
            "period": {"code": period.code, "label": period.label}, "run": {"id": run.id, "status": run.status},
            "rows": rows, "adjustments": entries}


def mc_comparison(db: Session, company: Company, period_ids: list[int], unit: str | None = None) -> dict:
    div, unit_name = unit_divisor(company, unit)
    periods, data = [], {}
    for pid in period_ids:
        period = db.get(Period, pid)
        if period is None or period.company_id != company.id:
            raise HTTPException(404, f"Period {pid} not found")
        run = latest_run(db, pid)
        if run is None:
            continue
        rep = mc_report(db, run, unit)
        periods.append({"id": pid, "code": period.code, "label": period.label, "run_id": run.id,
                        "run_status": run.status})
        for r in rep["rows"]:
            data.setdefault((r["plant"], r["pg"]), {})[period.code] = r
    rows = [{"plant": k[0], "pg": k[1], "by_period": {pc: {"sales": v["sales"], "mc": v["mc_worked_out"],
                                                           "mc_pct": v["mc_pct"]} for pc, v in d.items()}}
            for k, d in data.items()]
    return {"report": "mc_comparison", "unit": unit_name, "periods": periods, "rows": rows}


# ----------------------------------------------------------------------- MIS
def mis_check(db: Session, run: CalcRun, unit: str | None = None) -> dict:
    company, period, cfg = run_context(db, run)
    div, unit_name = unit_divisor(company, unit)
    tol = float(company_settings(company).get("mis_tolerance", 1.0))
    vals = value_lookup(db, run.id)
    cdiv = float(cfg.calc_unit_divisor)
    rows = []
    for m in db.scalars(select(MisLine).where(MisLine.period_id == period.id).order_by(MisLine.sort_order,
                                                                                       MisLine.id)).all():
        try:
            pnl = float(ex.evaluate(m.expr, lambda r: vals.get((r.body.partition("@")[0], ALL, TOTAL), 0.0)
                                    if r.kind == "L" else 0.0))
        except ex.ExpressionError as e:
            rows.append({"label": m.label, "error": str(e)})
            continue
        mis_rupees = m.mis_value                 # MIS values are stored in rupees
        diff = mis_rupees + pnl if m.compare == "sum" else mis_rupees - pnl
        rows.append({"label": m.label, "mis": mis_rupees / div, "pnl": pnl / div, "difference": diff / div,
                     "difference_rupees": diff, "ok": abs(diff) <= tol, "compare": m.compare, "expr": m.expr})
    return {"report": "mis", "title": "Check with MIS", "unit": unit_name, "tolerance_rupees": tol,
            "period": {"code": period.code, "label": period.label}, "run": {"id": run.id, "status": run.status},
            "rows": rows, "differences": sum(1 for r in rows if not r.get("ok", True))}


# ----------------------------------------------------------------------- summaries
def kpis(vals: dict, keys: dict[str, str], plant: str, code: str, div: float) -> dict:
    out = {}
    for k, line in keys.items():
        out[k] = vals.get((line, plant, code), 0.0) / div
    s = out.get("sales") or 0.0
    for k in ("material_cost", "gross_margin", "plant_profit", "pbt", "total_variable_cost"):
        if k in out:
            out[f"{k}_pct"] = out[k] / s if s else None
    return out


def management_summary(db: Session, run: CalcRun, unit: str | None = None) -> dict:
    company, period, cfg = run_context(db, run)
    div, unit_name = unit_divisor(company, unit)
    vals = value_lookup(db, run.id)
    keys = key_line_codes(cfg, get_template(db, company, "pg_wise", cfg))
    bep = keys.get("bep")
    months = period_months(period)
    plants = []
    for p in cfg.plants + [ALL]:
        k = kpis(vals, keys, p, TOTAL, div)
        if bep:
            k["bep_per_month"] = (k.get("bep") or 0.0) / months
        plants.append({"plant": p, **k})
    pgs = [{"pg": g, **kpis(vals, keys, ALL, g, div)} for g in cfg.product_groups + [cfg.com_code]]
    return {"report": "summary", "title": "Management summary", "unit": unit_name, "company": company.name,
            "period": {"code": period.code, "label": period.label, "months": months},
            "run": {"id": run.id, "status": run.status}, "plants": plants, "product_groups": pgs, "keys": keys}


def compare_runs(db: Session, run_a: CalcRun, run_b: CalcRun, level: str = "company", plant: str | None = None,
                 pg: str | None = None, unit: str | None = None) -> dict:
    if run_a.company_id != run_b.company_id:
        raise HTTPException(400, "Runs belong to different companies")
    company, period_a, cfg = run_context(db, run_a)
    period_b = db.get(Period, run_b.period_id)
    div, unit_name = unit_divisor(company, unit)
    va, vb = value_lookup(db, run_a.id), value_lookup(db, run_b.id)
    p_key = ALL if level == "company" or not plant else plant
    c_key = pg if (level == "pg" and pg) else TOTAL
    template = get_template(db, company, "pg_wise", cfg)
    rows = []
    for row in template:
        if not row.get("line"):
            continue
        a = va.get((row["line"], p_key, c_key), 0.0) / div
        b = vb.get((row["line"], p_key, c_key), 0.0) / div
        rows.append({"line": row["line"], "label": row["label"], "style": row.get("style") or {}, "a": a, "b": b,
                     "variance": a - b, "variance_pct": (a - b) / abs(b) if b else None})
    return {"report": "comparison", "unit": unit_name, "level": level, "plant": p_key, "pg": c_key,
            "a": {"run_id": run_a.id, "period": period_a.code, "scenario": period_a.scenario, "label": period_a.label},
            "b": {"run_id": run_b.id, "period": period_b.code, "scenario": period_b.scenario, "label": period_b.label},
            "rows": rows}


def dashboard(db: Session, company: Company, period_id: int | None, unit: str | None = None) -> dict:
    div, unit_name = unit_divisor(company, unit)
    periods = db.scalars(select(Period).where(Period.company_id == company.id, Period.scenario == "actual")
                         .order_by(Period.end_date)).all()
    if not periods:
        return {"unit": unit_name, "periods": [], "trend": [], "current": None}
    current = next((p for p in periods if p.id == period_id), None) or periods[-1]
    trend = []
    keys = None
    for p in periods:
        run = latest_run(db, p.id)
        if run is None:
            continue
        cfg = EngineConfig.from_dict(run.config_snapshot)
        keys = key_line_codes(cfg, get_template(db, company, "pg_wise", cfg))
        vals = value_lookup(db, run.id)
        trend.append({"period": p.code, "label": p.label, "run_status": run.status,
                      **kpis(vals, keys, ALL, TOTAL, div)})
    run = latest_run(db, current.id)
    cur = None
    if run is not None:
        summary = management_summary(db, run, unit)
        budget = db.scalar(select(Period).where(Period.company_id == company.id, Period.code == current.code,
                                                Period.scenario == "budget"))
        bud = None
        if budget is not None:
            brun = latest_run(db, budget.id)
            if brun is not None:
                bvals = value_lookup(db, brun.id)
                bud = kpis(bvals, summary["keys"], ALL, TOTAL, div)
        cur = {"period": current.code, "label": current.label, "run": {"id": run.id, "status": run.status},
               "company": next(p for p in summary["plants"] if p["plant"] == ALL),
               "plants": [p for p in summary["plants"] if p["plant"] != ALL],
               "product_groups": summary["product_groups"], "budget": bud,
               "controls": run.control_summary}
    return {"unit": unit_name, "periods": [{"id": p.id, "code": p.code, "label": p.label} for p in periods],
            "trend": trend, "current": cur}


# ----------------------------------------------------------------------- drill-down
def drilldown(db: Session, run: CalcRun, line: str, plant: str, code: str, unit: str | None = None,
              limit: int = 200) -> dict:
    """Explain one reported value: its components (category parts, PRE items, MC adjustments)
    and, for each, the GL x business-area / cost-centre rows behind it.  Allocated parts show
    the pool's rows scaled by the share that reached this cell; every part's rows add up to
    the part, and the parts add up to the reported value."""
    import pandas as pd

    company, period, cfg = run_context(db, run)
    div, unit_name = unit_divisor(company, unit)
    result = load_result(db, run)
    e = result.engine
    if line not in e.lines:
        raise HTTPException(404, f"Unknown line {line}")
    if plant not in e.pidx:
        raise HTTPException(400, "Drill-down needs a plant (cell or plant total)")
    if code != TOTAL and code not in e.kidx:
        raise HTTPException(400, f"Unknown product group {code}")
    cdiv = e.div / div                      # engine unit -> display unit
    gl_desc = {g: (info or {}).get("description") for g, info in e.cfg.gl_accounts.items()}

    def cells(p: str, c: str) -> list[tuple[str, str]]:
        return [(pp, cc) for pp in (e.plants if p == ALL else [p]) for cc in (e.codes if c == TOTAL else [c])]

    def scaled(rows: pd.DataFrame, dim: str, value_col: str, target: float) -> list[dict]:
        if rows is None or not len(rows):
            return []
        agg = rows.groupby(["gl", dim], as_index=False)[value_col].sum()
        total = float(agg[value_col].sum())
        ratio = target / total if abs(total) > 1e-15 else 0.0
        agg = agg.reindex(agg[value_col].abs().sort_values(ascending=False).index).head(limit)
        return [{"gl": r.gl, "gl_desc": gl_desc.get(r.gl), "dimension": getattr(r, dim),
                 "value": float(getattr(r, value_col)) * ratio * cdiv, "share": ratio}
                for r in agg.itertuples()]

    pre_ok = result.pre_facts[result.pre_facts["status"] == "ok"]
    parts = []
    for part in result.explain(line, plant, code):
        kind, body, p, c = part["kind"], part["body"], part["plant"], part["code"]
        item: dict[str, Any] = {"label": part["label"], "value": part["value"] * cdiv,
                                "coefficient": part["coefficient"], "kind": kind, "plant": p, "code": c,
                                "sources": []}
        if kind == "C":
            cat, _, comp = body.partition("|")
            frames = [result.source_rows(cat, comp, pp, cc) for pp, cc in cells(p, c)]
            frames = [f for f in frames if len(f)]
            item.update({"category": cat, "component": comp,
                         "pool": {"direct": None, "common": cfg.com_code, "corporate": cfg.cor_code}[comp]})
            if frames:
                rows = pd.concat(frames)
                item["sources"] = scaled(rows, "ba", "value", part["value"])
                if comp == "direct":
                    for s_ in item["sources"]:
                        s_["share"] = 1.0
        elif kind == "P":
            bits = body.split("|")
            plants_ = e.plants if p == ALL else [p]
            if bits[0] == "direct":
                codes_ = e.codes if c == TOTAL else [c]
                rows = pre_ok[(pre_ok["category"] == bits[1]) & pre_ok["plant"].isin(plants_)
                              & pre_ok["function"].isin(codes_)]
            else:
                fn = bits[-1]
                rows = pre_ok[pre_ok["function"] == fn]
                if bits[0] == "item":
                    rows = rows[rows["category"] == bits[1]]
                mode = (e.functions.get(fn) or {}).get("mode", "plant")
                if mode == "plant" or bits[0] == "pool":
                    rows = rows[rows["plant"].isin(plants_)]
            item["sources"] = scaled(rows, "cc", "amount", part["value"])
            if bits[0] == "direct":
                for s_ in item["sources"]:
                    s_["share"] = 1.0
        elif kind == "MCADJ":
            in_loc = set(cells(p, c))
            for m in db.scalars(select(McAdjustment).where(McAdjustment.period_id == period.id)).all():
                if (m.plant_code, m.pg_code) in in_loc:
                    item["sources"].append({
                        "gl": None, "dimension": f"{m.plant_code}|{m.pg_code}",
                        "value": m.value / div if m.kind == "amount" else None,
                        "note": (m.reason or "") + (f" (target MC {m.value * 100:.2f}% of sales)"
                                                    if m.kind == "mc_pct" else "")})
        parts.append(item)
    total = result.value(line, plant, None if code == TOTAL else code) * cdiv
    return {"line": line, "label": e.lines[line]["label"], "plant": plant, "code": code, "unit": unit_name,
            "value": total, "parts": parts, "explained": float(np.sum([p["value"] for p in parts]))}
