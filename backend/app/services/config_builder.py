"""Convert between database masters and the engine configuration."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..engine import EngineConfig
from ..models import (AllocationRule, BusinessArea, Category, Company, CostCentre, GLAccount, PnlLine, Plant,
                      PreFunction, ProductGroup, ReportTemplate)

DEFAULT_SETTINGS = {
    "calc_unit_divisor": 1e7,         # engine works in crore by default
    "display_unit": "crore",          # rupees | thousand | lakh | crore | million
    "number_format": "indian",        # indian (1,00,000) | international (100,000)
    "decimals": 2,
    "fiscal_year_start_month": 4,
    "com_code": "COM",
    "cor_code": "COR",
    "pre_categories": ["Wages", "Salaries", "Others"],
    "mis_tolerance": 1.0,             # rupees
    "meta": {},
    "ignore_columns": [],             # TB helper/total columns to skip on import
}

UNITS = {"rupees": 1.0, "thousand": 1e3, "lakh": 1e5, "million": 1e6, "crore": 1e7}


def company_settings(company: Company) -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(company.settings or {})
    return s


def active(model, company_id: int, on: dt.date | None):
    q = select(model).where(model.company_id == company_id)
    if on is not None and hasattr(model, "effective_from"):
        q = q.where(model.effective_from <= on, or_(model.effective_to.is_(None), model.effective_to >= on))
    return q


def build_engine_config(db: Session, company: Company, on: dt.date | None = None) -> EngineConfig:
    s = company_settings(company)
    plants = db.scalars(active(Plant, company.id, on).where(Plant.is_reporting.is_(True))
                        .order_by(Plant.sort_order, Plant.code)).all()
    pgs = db.scalars(active(ProductGroup, company.id, on).order_by(ProductGroup.sort_order, ProductGroup.code)).all()
    bas = db.scalars(active(BusinessArea, company.id, on)).all()
    gls = db.scalars(active(GLAccount, company.id, on)).all()
    ccs = db.scalars(active(CostCentre, company.id, on)).all()
    cats = db.scalars(active(Category, company.id, on).order_by(Category.sort_order, Category.id)).all()
    fns = db.scalars(active(PreFunction, company.id, on)).all()
    rules = db.scalars(active(AllocationRule, company.id, on).order_by(AllocationRule.sort_order,
                                                                       AllocationRule.id)).all()
    lines = db.scalars(active(PnlLine, company.id, on).order_by(PnlLine.sort_order, PnlLine.id)).all()
    return EngineConfig(
        plants=[p.code for p in plants], product_groups=[p.code for p in pgs],
        com_code=s["com_code"], cor_code=s["cor_code"], calc_unit_divisor=float(s["calc_unit_divisor"]),
        business_areas={b.code: {"plant": b.plant_code, "code": b.pg_code} for b in bas},
        gl_accounts={g.code: {"category": g.category, "type": g.gl_type, "description": g.description} for g in gls},
        cost_centres={c.code: {"plant": c.plant_code, "function": c.function, "barea": c.business_area} for c in ccs},
        categories=[{"name": c.name, "sign": c.sign} for c in cats],
        pre_categories=list(s["pre_categories"]),
        functions={f.code: {"group": f.group, "mode": f.mode} for f in fns},
        rules=[{"dataset": r.dataset, "category": r.category, "pool": r.pool, "mode": r.mode, "label": r.label,
                **(r.definition or {})} for r in rules],
        lines=[{"code": ln.code, "label": ln.label, "expr": ln.expr, **(ln.attrs or {})} for ln in lines],
        meta=dict(s.get("meta") or {}),
    )


RULE_KEYS = ("dataset", "category", "pool", "mode", "label")
LINE_KEYS = ("code", "label", "expr")


def save_engine_config(db: Session, company: Company, cfg: dict, effective_from: dt.date = dt.date(2000, 1, 1),
                       extra: dict | None = None, replace: bool = True) -> dict:
    """Write an engine configuration (e.g. from a legacy workbook or the demo generator) as masters."""
    extra = extra or {}
    if replace:
        for model in (Plant, ProductGroup, BusinessArea, GLAccount, CostCentre, Category, PreFunction,
                      AllocationRule, PnlLine):
            db.query(model).filter(model.company_id == company.id).delete(synchronize_session=False)
    plant_names = extra.get("plant_names", {})
    for i, p in enumerate(cfg["plants"]):
        db.add(Plant(company_id=company.id, code=p, name=plant_names.get(p, p), sort_order=i,
                     effective_from=effective_from))
    families = extra.get("families", {})
    pg_names = extra.get("pg_names", {})
    for i, g in enumerate(cfg["product_groups"]):
        db.add(ProductGroup(company_id=company.id, code=g, name=pg_names.get(g, g), family=families.get(g),
                            sort_order=i, effective_from=effective_from))
    ba_extra = extra.get("business_areas", {})
    for code, info in cfg["business_areas"].items():
        e = ba_extra.get(code, {})
        db.add(BusinessArea(company_id=company.id, code=code, name=e.get("name") or "", plant_code=info.get("plant"),
                            pg_code=info.get("code"), family=e.get("family"), effective_from=effective_from))
    gl_extra = extra.get("gl_accounts", {})
    for code, info in cfg["gl_accounts"].items():
        e = gl_extra.get(code, {})
        db.add(GLAccount(company_id=company.id, code=code, description=str(info.get("description") or
                                                                           e.get("description") or "")[:300],
                         fs_description=(e.get("fs_description") or None), category=info.get("category"),
                         gl_type=info.get("type") or "NONPRE", note=e.get("note"), effective_from=effective_from))
    cc_extra = extra.get("cost_centres", {})
    for code, info in cfg["cost_centres"].items():
        e = cc_extra.get(code, {})
        db.add(CostCentre(company_id=company.id, code=code, description=str(e.get("description") or "")[:300],
                          business_area=info.get("barea"), plant_code=info.get("plant"),
                          function=info.get("function"), effective_from=effective_from))
    for i, c in enumerate(cfg["categories"]):
        db.add(Category(company_id=company.id, name=c["name"], sign=int(c.get("sign", 1)), sort_order=i,
                        group_name=c.get("group"), effective_from=effective_from))
    for code, info in (cfg.get("functions") or {}).items():
        db.add(PreFunction(company_id=company.id, code=code, name=info.get("name", code),
                           group=info.get("group") or "factory", mode=info.get("mode") or "plant",
                           effective_from=effective_from))
    for i, r in enumerate(cfg["rules"]):
        definition = {k: v for k, v in r.items() if k not in RULE_KEYS}
        db.add(AllocationRule(company_id=company.id, dataset=r.get("dataset", "NONPRE"), category=r["category"],
                              pool=r["pool"], mode=r.get("mode") or "plant", label=r.get("label"),
                              definition=definition, sort_order=i, effective_from=effective_from))
    for i, ln in enumerate(cfg["lines"]):
        attrs = {k: v for k, v in ln.items() if k not in LINE_KEYS}
        db.add(PnlLine(company_id=company.id, code=ln["code"], label=ln["label"], expr=ln.get("expr") or "0",
                       sort_order=i, attrs=attrs, effective_from=effective_from))
    s = dict(company.settings or {})
    s.update({"calc_unit_divisor": cfg.get("calc_unit_divisor", 1e7), "com_code": cfg.get("com_code", "COM"),
              "cor_code": cfg.get("cor_code", "COR"), "pre_categories": cfg.get("pre_categories"),
              "meta": cfg.get("meta") or {}})
    company.settings = s
    db.flush()
    return {"plants": len(cfg["plants"]), "product_groups": len(cfg["product_groups"]),
            "business_areas": len(cfg["business_areas"]), "gl_accounts": len(cfg["gl_accounts"]),
            "cost_centres": len(cfg["cost_centres"]), "categories": len(cfg["categories"]),
            "rules": len(cfg["rules"]), "lines": len(cfg["lines"])}


def save_templates(db: Session, company: Company, templates: dict[str, list]) -> None:
    for name, rows in templates.items():
        t = db.scalar(select(ReportTemplate).where(ReportTemplate.company_id == company.id,
                                                   ReportTemplate.name == name))
        if t is None:
            db.add(ReportTemplate(company_id=company.id, name=name, rows=rows))
        else:
            t.rows = rows


def default_template(cfg: EngineConfig) -> list[dict]:
    rows = []
    for ln in cfg.lines:
        if ln.get("hidden"):
            continue
        style = ln.get("style") or {}
        rows.append({"kind": "line", "label": ln["label"], "line": ln["code"], "pct_base": ln.get("pct_base"),
                     "style": style})
    return rows


def get_template(db: Session, company: Company, name: str, cfg: EngineConfig) -> list[dict]:
    t = db.scalar(select(ReportTemplate).where(ReportTemplate.company_id == company.id,
                                               ReportTemplate.name == name))
    if t is not None and t.rows:
        return t.rows
    if name != "pg_wise":
        t = db.scalar(select(ReportTemplate).where(ReportTemplate.company_id == company.id,
                                                   ReportTemplate.name == "pg_wise"))
        if t is not None and t.rows:
            return t.rows
    return default_template(cfg)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, float) and value != value:
        return None
    return value
