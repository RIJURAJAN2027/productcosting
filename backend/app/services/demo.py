"""A fictional demo company with synthetic data (no real company figures).

"Demo Flow Controls Pvt Ltd" has three manufacturing plants and a head office,
eight product groups, Common and Corporate cost pools, PRE (personnel cost) by
cost centre and service function, drivers, adjustments, MC adjustments, MIS
figures, a budget and three YTD periods so every screen has data.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import (AdjustmentRule, Company, DriverValue, McAdjustment, MisLine, Period, Tenant, User)
from ..security import hash_password
from .config_builder import save_engine_config, save_templates
from .uploads import store_tb

CR = 1e7
PLANTS = [("NTH", "North Works"), ("STH", "South Works"), ("WST", "West Works"), ("HQ", "Head Office")]
PGS = [("GATE", "Gate valves", "Linear"), ("GLOBE", "Globe valves", "Linear"), ("CHECK", "Check valves", "Linear"),
       ("BALL", "Ball valves", "Rotary"), ("BFLY", "Butterfly valves", "Rotary"), ("PLUG", "Plug valves", "Rotary"),
       ("SPCL", "Special projects", "Special"), ("TRADED", "Traded goods", "Traded")]
# annual sales (Rs crore) by plant x product group
SALES = {
    "NTH": {"GATE": 120, "GLOBE": 80, "CHECK": 40, "BALL": 0, "BFLY": 0, "PLUG": 0, "SPCL": 30, "TRADED": 15},
    "STH": {"GATE": 0, "GLOBE": 0, "CHECK": 10, "BALL": 90, "BFLY": 60, "PLUG": 25, "SPCL": 0, "TRADED": 8},
    "WST": {"GATE": 20, "GLOBE": 15, "CHECK": 5, "BALL": 10, "BFLY": 30, "PLUG": 0, "SPCL": 45, "TRADED": 0},
    "HQ": {g: 0 for g, _, _ in PGS},
}
EXPORT_SHARE = {"GATE": 0.55, "GLOBE": 0.45, "CHECK": 0.3, "BALL": 0.6, "BFLY": 0.35, "PLUG": 0.2, "SPCL": 0.7,
                "TRADED": 0.1}
MC_PCT = {"GATE": 0.52, "GLOBE": 0.55, "CHECK": 0.5, "BALL": 0.58, "BFLY": 0.6, "PLUG": 0.54, "SPCL": 0.62,
          "TRADED": 0.82}

# category -> (sign, GL list [(code, description)], P&L group)
CATEGORIES = [
    ("Sales-Domestic", -1, [("50000", "Sales - Domestic"), ("50005", "Sales Trading Goods - Domestic")], "sales"),
    ("Sales-Exports", -1, [("50001", "Sales - Exports")], "sales"),
    ("Sales Returns", -1, [("50012", "Sales Returns - Domestic")], "sales"),
    ("Liquidated damages", -1, [("50010", "Liquidated Damages")], "sales"),
    ("Sales of services", -1, [("50040", "Service Income")], "sales"),
    ("Bill of materials", 1, [("52000", "Raw Material Consumption"), ("52002", "Raw Material Inventory Change"),
                              ("52012", "Finished Goods Inventory Change")], "mc"),
    ("Subcontracting charges", 1, [("60006", "Sub Contracting Charges")], "mc"),
    ("Freight inward", 1, [("60100", "Freight Inward")], "mc"),
    ("Purchase price variance", 1, [("52300", "Purchase Price Variance")], "mc"),
    ("Scrap", 1, [("52251", "Scrap Sales")], "mc"),
    ("Packing", 1, [("63000", "Packing Materials")], "variable"),
    ("Forwarding charges", 1, [("63100", "Forwarding Charges")], "variable"),
    ("Warranty", 1, [("63200", "Warranty Expenses")], "variable"),
    ("Consumables", 1, [("61000", "Factory Consumables")], "variable"),
    ("Small tools", 1, [("61100", "Small Tools")], "variable"),
    ("Power & Fuel", 1, [("64000", "Power & Fuel")], "foh"),
    ("Repairs to P&M", 1, [("64100", "Repairs to Plant & Machinery")], "foh"),
    ("Repairs to Building", 1, [("64110", "Repairs to Building")], "foh"),
    ("Factory maintenance", 1, [("64120", "Factory & Stores Maintenance")], "foh"),
    ("Contract manpower", 1, [("64200", "Contract Manpower Charges")], "foh"),
    ("Professional charges", 1, [("64210", "Professional Charges")], "foh"),
    ("Travelling", 1, [("64300", "Travelling & Conveyance")], "foh"),
    ("Rent", 1, [("64310", "Rent")], "foh"),
    ("Telephone", 1, [("64320", "Telephone & Internet")], "foh"),
    ("Printing & Stationery", 1, [("64330", "Printing & Stationery")], "foh"),
    ("Depreciation", 1, [("66000", "Depreciation")], "foh"),
    ("Selling commission", 1, [("65000", "Selling Commission")], "corp"),
    ("Advertisement", 1, [("65010", "Advertisement")], "corp"),
    ("Insurance", 1, [("64400", "Insurance")], "corp"),
    ("Bank charges", 1, [("65100", "Bank Charges")], "corp"),
    ("Finance cost", 1, [("65200", "Interest on Working Capital")], "corp"),
    ("Audit fees", 1, [("65300", "Audit Fees")], "corp"),
    ("CSR", 1, [("65400", "CSR Expenditure")], "corp"),
    ("Other income", -1, [("51000", "Interest Income"), ("51030", "Miscellaneous Income")], "other"),
    ("Export benefits", -1, [("51040", "Export Incentives")], "other"),
    ("Forex", 1, [("50003", "Exchange Variation"), ("50070", "Forward Contract MTM")], "other_cost"),
    ("Inventory provisions", 1, [("60060", "Provision for Non-moving Inventory"),
                                 ("60061", "Provision for Slow-moving Inventory")], "other_cost"),
    ("Provision for doubtful debts", 1, [("65500", "Provision for Doubtful Debts")], "other_cost"),
]
PRE_GLS = [("62000", "Wages - Permanent Workmen", "Wages"), ("62002", "Wages - Overtime", "Wages"),
           ("62011", "Salaries", "Salaries"), ("62030", "Provident Fund Contribution", "Salaries"),
           ("62080", "Medical Reimbursement", "Others"), ("62085", "Training Expenses", "Others")]
FUNCTIONS = {"QC": ("Quality control", "factory", "plant"), "STORES": ("Stores", "factory", "plant"),
             "MAINT": ("Maintenance", "factory", "plant"), "COM": ("Plant common", "factory", "plant"),
             "RND": ("Research & development", "factory", "pooled"), "SALES": ("Sales office", "corporate", "pooled"),
             "COR": ("Corporate office", "corporate", "pooled")}
PERIODS = [("2026-06", "YTD Apr-Jun 2026", dt.date(2026, 6, 30), 3),
           ("2026-09", "YTD Apr-Sep 2026", dt.date(2026, 9, 30), 6),
           ("2026-12", "YTD Apr-Dec 2026", dt.date(2026, 12, 31), 9)]


def _line(code, label, expr, **kw):
    return {"code": code, "label": label, "expr": expr, **kw}


def demo_config() -> dict:
    pg_codes = [p[0] for p in PGS]
    plants = [p[0] for p in PLANTS]
    bas = {}
    for p in plants:
        for g in pg_codes + ["COM", "COR"]:
            if p == "HQ" and g not in ("COR",):
                continue
            bas[f"{p}_{g}"] = {"plant": p, "code": g}
    gls = {}
    for name, sign, gl_list, _ in CATEGORIES:
        for code, desc in gl_list:
            gls[code] = {"category": name, "type": "NONPRE", "description": desc}
    for code, desc, cat in PRE_GLS:
        gls[code] = {"category": cat, "type": "PRE", "description": desc}
    gls["69000"] = {"category": None, "type": "EXCLUDED", "description": "Purchase clearing (offsetting)"}
    ccs = {}
    for p in plants[:3]:
        for g in pg_codes:
            ccs[f"{p}-{g}"] = {"plant": p, "function": g, "barea": f"{p}_{g}"}
        for fn in ("QC", "STORES", "MAINT", "COM"):
            ccs[f"{p}-{fn}"] = {"plant": p, "function": fn, "barea": f"{p}_COM"}
    ccs.update({"HQ-HR": {"plant": "HQ", "function": "COR", "barea": "HQ_COR"},
                "HQ-FIN": {"plant": "HQ", "function": "COR", "barea": "HQ_COR"},
                "HQ-RND": {"plant": "HQ", "function": "RND", "barea": "HQ_COR"},
                "HQ-SALES": {"plant": "HQ", "function": "SALES", "barea": "HQ_COR"}})
    cats = [{"name": n, "sign": s, "group": grp} for n, s, _, grp in CATEGORIES]

    def comp(cat, c="direct"):
        return {"kind": "component", "category": cat, "component": c}
    rules = []
    basis_for = {
        "Sales-Domestic": comp("Sales-Domestic"), "Sales-Exports": comp("Sales-Exports"),
        "Sales Returns": comp("Sales-Domestic"), "Liquidated damages": comp("Sales-Domestic"),
        "Sales of services": {"kind": "sum", "items": [comp("Sales-Domestic"), comp("Sales-Exports")]},
        "Bill of materials": comp("Bill of materials"), "Subcontracting charges": comp("Bill of materials"),
        "Freight inward": comp("Bill of materials"), "Purchase price variance": comp("Bill of materials"),
        "Scrap": comp("Bill of materials"),
        "Consumables": {"kind": "driver", "driver": "machine_hours"},
        "Small tools": {"kind": "driver", "driver": "machine_hours"},
        "Power & Fuel": {"kind": "driver", "driver": "power_units"},
        "Repairs to P&M": {"kind": "driver", "driver": "machine_hours"},
        "Factory maintenance": {"kind": "driver", "driver": "machine_hours"},
        "Contract manpower": {"kind": "line", "line": "material_cost"},
        "Professional charges": {"kind": "line", "line": "material_cost"},
        "Forex": {"kind": "line", "line": "export_sales_net"},
        "Export benefits": comp("Sales-Exports"),
        "Forwarding charges": {"kind": "line", "line": "sales_total"},
    }
    for name, sign, _, grp in CATEGORIES:
        basis = basis_for.get(name, {"kind": "line", "line": "sales_total"})
        rules.append({"dataset": "NONPRE", "category": name, "pool": "COM", "mode": "plant", "basis": basis,
                      "label": f"{name} - Common"})
        # sales and material categories are allocated on direct amounts (allocating them on total sales
        # would make sales depend on itself); everything else on post-allocation total sales
        corp_basis = basis if grp in ("sales", "mc") else {"kind": "line", "line": "sales_total"}
        rules.append({"dataset": "NONPRE", "category": name, "pool": "COR", "mode": "pooled", "basis": corp_basis,
                      "label": f"{name} - Corporate"})
    for cat in ("Wages", "Salaries", "Others"):
        for fn, (_, group, mode) in FUNCTIONS.items():
            basis = {"kind": "line", "line": "material_cost"} if fn in ("QC", "STORES", "MAINT") else \
                {"kind": "line", "line": "sales_total"}
            rules.append({"dataset": "PRE", "category": cat, "pool": fn, "mode": mode, "basis": basis,
                          "label": f"PRE {cat} - {fn}"})

    def total(names):
        return " + ".join(f"[C:{n}|total]" for n in names)
    foh = [n for n, _, _, g in CATEGORIES if g == "foh"]
    corp = [n for n, _, _, g in CATEGORIES if g == "corp"]
    B = {"bold": True}
    H = {"bold": True, "fill": "FFDCE6F1"}
    lines = [
        _line("sales_summary", "Sales", "[L:sales_total]", style=H, pct_base="[L:sales_total]"),
        _line("material_cost_summary", "Material Cost", "[L:material_cost]", style=B, pct_base="[L:sales_total]"),
        _line("variable_cost_summary", "Variable cost", "[L:variable_cost]", pct_base="[L:sales_total]"),
        _line("total_variable_cost", "Total variable cost", "[L:material_cost] + [L:variable_cost]", style=B,
              pct_base="[L:sales_total]"),
        _line("gross_margin", "Gross margin", "[L:sales_total] - [L:total_variable_cost]", style=H,
              pct_base="[L:sales_total]"),
        _line("factory_overhead", "Factory overhead", "[L:factory_overhead_total]", pct_base="[L:sales_total]"),
        _line("pre_factory", "PRE-factory related", "[P:group|factory]", pct_base="[L:sales_total]"),
        _line("plant_profit", "Plant profit", "[L:gross_margin] - [L:factory_overhead] - [L:pre_factory]", style=H,
              pct_base="[L:sales_total]"),
        _line("corporate_overheads", "Corporate overheads", "[L:corp_overheads_total]", pct_base="[L:sales_total]"),
        _line("pre_corporate", "PRE-corporate", "[P:group|corporate]", pct_base="[L:sales_total]"),
        _line("other_credits", "Other credits/ (debits)", "[L:other_credits_total]", pct_base="[L:sales_total]"),
        _line("pbt", "PBT", "[L:plant_profit] - [L:corporate_overheads] - [L:pre_corporate] + [L:other_credits]",
              style=H, pct_base="[L:sales_total]"),
        _line("bep", "BEP", "([L:factory_overhead] + [L:pre_factory] + [L:corporate_overheads] + [L:pre_corporate])"
                            " / ([L:gross_margin] / [L:sales_total])", additive=False, style={"italic": True}),
        _line("foh_direct_pg", "Factory overhead - Direct - PG", " + ".join(f"[C:{n}|direct]" for n in foh),
              pct_base="[L:sales_total]"),
        _line("foh_alloc_pg", "Factory overhead - Allocation - PG",
              " + ".join(f"[C:{n}|common] + [C:{n}|corporate]" for n in foh), pct_base="[L:sales_total]"),
        _line("pre_direct_pg", "PRE-factory related - Direct - PG", "[P:direct]", pct_base="[L:sales_total]"),
        _line("pre_alloc_pg", "PRE-factory related - Allocation - PG", "[P:group|factory] - [P:direct]",
              pct_base="[L:sales_total]"),
        _line("sales_domestic", "Sales-Domestic - Total", "[C:Sales-Domestic|total]", pct_base="[L:sales_total]"),
        _line("sales_returns", "Sales Returns - Total", "[C:Sales Returns|total]", pct_base="[L:sales_total]"),
        _line("ld", "Liquidated damages - Total", "[C:Liquidated damages|total]", pct_base="[L:sales_total]"),
        _line("domestic_sales_net", "Domestic sales - Net", "[L:sales_domestic] + [L:sales_returns] + [L:ld]",
              style=B, pct_base="[L:sales_total]"),
        _line("export_sales_net", "Export sales - Net", "[C:Sales-Exports|total]", style=B,
              pct_base="[L:sales_total]"),
        _line("services", "Sales of services - Total", "[C:Sales of services|total]", pct_base="[L:sales_total]"),
        _line("sales_total", "Sales - Total", "[L:domestic_sales_net] + [L:export_sales_net] + [L:services]",
              style=H, pct_base="[L:sales_total]"),
    ]
    mc_names = [n for n, _, _, g in CATEGORIES if g == "mc"]
    for n in mc_names:
        lines.append(_line("mc_" + n.lower().replace(" ", "_"), f"{n} - Total", f"[C:{n}|total]",
                           pct_base="[L:sales_total]"))
    lines.append(_line("material_cost_tb", "Material cost per TB",
                       " + ".join("[L:mc_" + n.lower().replace(" ", "_") + "]" for n in mc_names),
                       pct_base="[L:sales_total]", hidden=True))
    lines.append(_line("mc_adjustment", "Worked-out MC adjustment", "[MCADJ]", pct_base="[L:sales_total]"))
    lines.append(_line("material_cost", "Material cost - Total", "[L:material_cost_tb] + [L:mc_adjustment]",
                       style=H, pct_base="[L:sales_total]"))
    var_names = [n for n, _, _, g in CATEGORIES if g == "variable"]
    for n in var_names:
        lines.append(_line("vc_" + n.lower().replace(" ", "_"), f"{n} - Total", f"[C:{n}|total]",
                           pct_base="[L:sales_total]"))
    lines.append(_line("variable_cost", "Variable cost - Total",
                       " + ".join("[L:vc_" + n.lower().replace(" ", "_") + "]" for n in var_names), style=H,
                       pct_base="[L:sales_total]"))
    for n in foh:
        lines.append(_line("foh_" + n.lower().replace(" ", "_").replace("&", "and"), n,
                           f"[C:{n}|direct] + [C:{n}|common]", pct_base="[L:sales_total]"))
    lines.append(_line("factory_overhead_total", "Factory overhead - Total",
                       " + ".join("[L:foh_" + n.lower().replace(" ", "_").replace("&", "and") + "]" for n in foh),
                       style=H, pct_base="[L:sales_total]"))
    for n in foh:
        lines.append(_line("corp_" + n.lower().replace(" ", "_").replace("&", "and"), f"{n} - Corporate",
                           f"[C:{n}|corporate]", pct_base="[L:sales_total]"))
    for n in corp:
        lines.append(_line("corp_" + n.lower().replace(" ", "_").replace("&", "and"), f"{n} - Total",
                           f"[C:{n}|total]", pct_base="[L:sales_total]"))
    lines.append(_line("corp_overheads_total", "Corp overheads - Total",
                       " + ".join("[L:corp_" + n.lower().replace(" ", "_").replace("&", "and") + "]"
                                  for n in foh + corp), style=H, pct_base="[L:sales_total]"))
    lines += [
        _line("oc_other_income", "Other income - Total", "[C:Other income|total]", pct_base="[L:sales_total]"),
        _line("oc_export_benefits", "Export benefits - Total", "[C:Export benefits|total]",
              pct_base="[L:export_sales_net]"),
        _line("oc_inventory_prov", "Inventory provisions - Total", "-[C:Inventory provisions|total]",
              pct_base="[L:sales_total]"),
        _line("oc_forex", "Forex - Total", "-[C:Forex|total]", pct_base="[L:export_sales_net]"),
        _line("oc_pdd", "Provision For Doubtful Debts - Total", "-[C:Provision for doubtful debts|total]",
              pct_base="[L:sales_total]"),
        _line("other_credits_total", "Other credits/ (debits) - Total",
              "[L:oc_other_income] + [L:oc_export_benefits] + [L:oc_inventory_prov] + [L:oc_forex] + [L:oc_pdd]",
              style=H, pct_base="[L:sales_total]"),
        _line("pre_cost_total", "PRE Cost - Total", "[L:pre_factory] + [L:pre_corporate]", style=B,
              pct_base="[L:sales_total]"),
    ]
    return {"plants": plants, "product_groups": pg_codes, "com_code": "COM", "cor_code": "COR",
            "calc_unit_divisor": CR, "business_areas": bas, "gl_accounts": gls, "cost_centres": ccs,
            "categories": cats, "pre_categories": ["Wages", "Salaries", "Others"],
            "functions": {k: {"name": v[0], "group": v[1], "mode": v[2]} for k, v in FUNCTIONS.items()},
            "rules": rules, "lines": lines,
            "meta": {"pbt_line": "pbt", "sales_line": "sales_total", "material_cost_line": "material_cost",
                     "mc_tb_line": "material_cost_tb"}}


def demo_template(cfg: dict) -> list[dict]:
    order = ["sales_summary", "material_cost_summary", "variable_cost_summary", "total_variable_cost",
             "gross_margin", "factory_overhead", "pre_factory", "plant_profit", "corporate_overheads",
             "pre_corporate", "other_credits", "pbt", "bep", None, "foh_direct_pg", "foh_alloc_pg", "pre_direct_pg",
             "pre_alloc_pg", None, "sales_domestic", "sales_returns", "ld", "domestic_sales_net", None,
             "export_sales_net", "services", "sales_total", None]
    lines = {ln["code"]: ln for ln in cfg["lines"]}
    rest = [ln["code"] for ln in cfg["lines"] if ln["code"] not in order]
    rows = []
    for code in order + rest:
        if code is None:
            rows.append({"kind": "blank"})
            continue
        ln = lines[code]
        rows.append({"kind": "line", "label": ln["label"], "line": code, "pct_base": ln.get("pct_base"),
                     "style": ln.get("style") or {}})
    return rows


def _tb_for_period(rng: np.random.Generator, months: int, scale: float = 1.0) -> tuple[pd.DataFrame, pd.DataFrame,
                                                                                       dict]:
    f = months / 12 * scale
    rows = []

    def add(gl, ba, amount):
        if abs(amount) > 1:
            rows.append((gl, ba, round(amount, 2)))
    machine_hours, power_units = {}, {}
    for p, sales_by_pg in SALES.items():
        plant_sales = sum(sales_by_pg.values()) * f * CR
        for g, s in sales_by_pg.items():
            s = s * f * CR * rng.uniform(0.94, 1.06)
            if s <= 0:
                continue
            ba = f"{p}_{g}"
            exp = s * EXPORT_SHARE[g]
            add("50000", ba, -(s - exp) * 0.97)
            add("50005", ba, -(s - exp) * 0.03 if g == "TRADED" else 0)
            add("50001", ba, -exp)
            add("50012", ba, (s - exp) * rng.uniform(0.005, 0.015))
            add("50010", ba, s * rng.uniform(0.0, 0.01))
            mc = s * MC_PCT[g] * rng.uniform(0.97, 1.03)
            add("52000", ba, mc * 0.86)
            add("52002", ba, mc * rng.uniform(-0.03, 0.03))
            add("52012", ba, mc * rng.uniform(-0.02, 0.02))
            add("60006", ba, mc * 0.08)
            add("60100", ba, mc * 0.025)
            add("52300", ba, mc * rng.uniform(-0.01, 0.01))
            add("52251", ba, -mc * 0.012)
            add("63000", ba, s * 0.011)
            add("63100", ba, s * 0.017)
            add("63200", ba, s * 0.006)
            add("61000", ba, s * 0.009)
            add("61100", ba, s * 0.006)
            add("64000", ba, s * 0.012)
            add("64100", ba, s * 0.006)
            add("64200", ba, s * 0.015)
            add("66000", ba, s * 0.01)
            add("65000", ba, exp * 0.02)
            machine_hours[f"{p}|{g}"] = round(s / CR * rng.uniform(80, 120), 1)
            power_units[f"{p}|{g}"] = round(s / CR * rng.uniform(900, 1300), 0)
        if p != "HQ":
            com = f"{p}_COM"
            add("50000", com, -plant_sales * 0.01)
            add("50040", com, -plant_sales * 0.004)
            add("52000", com, plant_sales * 0.004)
            add("61000", com, plant_sales * 0.004)
            add("64000", com, plant_sales * 0.018)
            add("64100", com, plant_sales * 0.007)
            add("64110", com, plant_sales * 0.003)
            add("64120", com, plant_sales * 0.004)
            add("64200", com, plant_sales * 0.006)
            add("64210", com, plant_sales * 0.002)
            add("64300", com, plant_sales * 0.004)
            add("64310", com, plant_sales * 0.002)
            add("64320", com, plant_sales * 0.001)
            add("64330", com, plant_sales * 0.0006)
            add("66000", com, plant_sales * 0.012)
            add("64400", com, plant_sales * 0.0015)
            add("51030", com, -plant_sales * 0.002)
            add("60060", com, plant_sales * 0.002)
            cor = f"{p}_COR"
            add("64300", cor, plant_sales * 0.0015)
            add("65100", cor, plant_sales * 0.0008)
    total_sales = sum(sum(v.values()) for v in SALES.values()) * f * CR
    hq = "HQ_COR"
    for gl, pct in (("64300", 0.003), ("64310", 0.002), ("64320", 0.001), ("64330", 0.0005), ("64210", 0.003),
                    ("65010", 0.002), ("64400", 0.001), ("65100", 0.001), ("65200", 0.006), ("65300", 0.0003),
                    ("65400", 0.0015), ("66000", 0.002), ("65500", 0.0015), ("60061", 0.001)):
        add(gl, hq, total_sales * pct * rng.uniform(0.9, 1.1))
    add("51000", hq, -total_sales * 0.004)
    add("51040", hq, -total_sales * 0.009)
    add("50003", hq, total_sales * rng.uniform(-0.002, 0.003))
    add("50070", hq, total_sales * 0.0012)
    add("69000", hq, total_sales * 0.01)
    add("69000", "NTH_COM", -total_sales * 0.01)
    # PRE also appears in the BA TB (the engine takes PRE from the CC TB and cross-checks the totals)
    cc_rows = []
    for p in ("NTH", "STH", "WST"):
        plant_sales = sum(SALES[p].values()) * f * CR
        for g, s in SALES[p].items():
            if s:
                cc_rows.append(("62000", f"{p}-{g}", s * f * CR * 0.035))
                cc_rows.append(("62002", f"{p}-{g}", s * f * CR * 0.004))
                cc_rows.append(("62011", f"{p}-{g}", s * f * CR * 0.012))
        for fn, pct in (("QC", 0.006), ("STORES", 0.003), ("MAINT", 0.005), ("COM", 0.006)):
            cc_rows.append(("62011", f"{p}-{fn}", plant_sales * pct))
            cc_rows.append(("62030", f"{p}-{fn}", plant_sales * pct * 0.12))
            cc_rows.append(("62080", f"{p}-{fn}", plant_sales * pct * 0.05))
    for cc, pct in (("HQ-HR", 0.004), ("HQ-FIN", 0.005), ("HQ-RND", 0.006), ("HQ-SALES", 0.007)):
        cc_rows.append(("62011", cc, total_sales * pct))
        cc_rows.append(("62030", cc, total_sales * pct * 0.12))
        cc_rows.append(("62085", cc, total_sales * pct * 0.03))
    cc = pd.DataFrame([(g, c, round(a * rng.uniform(0.97, 1.03), 2)) for g, c, a in cc_rows],
                      columns=["gl", "dim", "amount"])
    cc_ba = cc.copy()
    cc_ba["dim"] = cc_ba["dim"].map(lambda c: c.replace("-", "_") if c.split("-")[1] in [g for g, _, _ in PGS]
                                    else (c.split("-")[0] + "_COM" if not c.startswith("HQ") else "HQ_COR"))
    ba = pd.DataFrame(rows, columns=["gl", "dim", "amount"])
    ba = pd.concat([ba, cc_ba], ignore_index=True).groupby(["gl", "dim"], as_index=False)["amount"].sum()
    descs = {}
    for _, _, gl_list, _ in CATEGORIES:
        descs.update(dict(gl_list))
    descs.update({c: d for c, d, _ in PRE_GLS})
    descs["69000"] = "Purchase clearing (offsetting)"
    ba["gl_desc"] = ba["gl"].map(descs).fillna("")
    cc["gl_desc"] = cc["gl"].map(descs).fillna("")
    return ba, cc, {"machine_hours": machine_hours, "power_units": power_units}


def create_demo_company(db: Session, tenant: Tenant, admin: User | None = None, code: str = "DEMO") -> Company:
    existing = db.scalar(select(Company).where(Company.tenant_id == tenant.id, Company.code == code))
    if existing is not None:
        return existing
    company = Company(tenant_id=tenant.id, code=code, name="Demo Flow Controls Pvt Ltd (fictional)", currency="INR",
                      is_demo=True, settings={"display_unit": "crore", "number_format": "indian", "decimals": 2,
                                              "fiscal_year_start_month": 4})
    db.add(company)
    db.flush()
    cfg = demo_config()
    save_engine_config(db, company, cfg, extra={"families": {g: fam for g, _, fam in PGS},
                                                "pg_names": {g: n for g, n, _ in PGS},
                                                "plant_names": dict(PLANTS)})
    save_templates(db, company, {"pg_wise": demo_template(cfg), "plant_wise": demo_template(cfg)})
    rng = np.random.default_rng(2026)
    periods = []
    for code_, label, end, months in PERIODS:
        p = Period(company_id=company.id, code=code_, label=label, end_date=end, start_date=dt.date(2026, 4, 1),
                   months=months, basis="YTD", fiscal_year="FY2026-27")
        db.add(p)
        db.flush()
        periods.append(p)
        ba, cc, drivers = _tb_for_period(rng, months)
        store_tb(db, company, p, "BA_TB", f"demo_BA_TB_{code_}.xlsx", None, ba,
                 {"source": "demo generator", "total": float(ba["amount"].sum())}, [], admin)
        store_tb(db, company, p, "CC_TB", f"demo_CC_TB_{code_}.xlsx", None, cc,
                 {"source": "demo generator", "total": float(cc["amount"].sum())}, [], admin)
        for name, values in drivers.items():
            for key, v in values.items():
                pl, g = key.split("|")
                db.add(DriverValue(company_id=company.id, period_id=p.id, driver=name, plant_code=pl, pg_code=g,
                                   value=v))
        db.add(McAdjustment(company_id=company.id, period_id=p.id, plant_code="WST", pg_code="SPCL", kind="mc_pct",
                            value=0.64, reason="Worked-out MC% per SAP product-cost report (demo)",
                            author_id=admin.id if admin else None))
    # standing adjustment rules (apply to every period)
    db.add(AdjustmentRule(company_id=company.id, period_id=None, type="regroup_gl",
                          definition={"tb": "BA", "from_gls": ["60061"], "to_gl": "60060"},
                          reason="Slow-moving provision reported with non-moving inventory provision",
                          author_name="Demo admin", sort_order=1))
    db.add(AdjustmentRule(company_id=company.id, period_id=None, type="exclude_gl",
                          definition={"tb": "BA", "gls": ["50070"]},
                          reason="Forward-contract MTM excluded from product costing (treasury result)",
                          author_name="Demo admin", sort_order=2))
    db.add(AdjustmentRule(company_id=company.id, period_id=periods[1].id, type="reclass",
                          definition={"tb": "BA", "from": {"gl": "52000", "dim": "STH_BALL"},
                                      "to": {"gl": "52000", "dim": "STH_BFLY"}, "amount": 1.45e6},
                          reason="Material booked to the wrong business area (sales order SO-1001)",
                          author_name="Demo analyst", sort_order=3))
    # budget for the half-year
    bud = Period(company_id=company.id, code="2026-09", label="Budget Apr-Sep 2026", scenario="budget",
                 end_date=dt.date(2026, 9, 30), start_date=dt.date(2026, 4, 1), months=6, basis="YTD",
                 fiscal_year="FY2026-27")
    db.add(bud)
    db.flush()
    ba, cc, drivers = _tb_for_period(np.random.default_rng(99), 6, scale=1.04)
    store_tb(db, company, bud, "BA_TB", "demo_budget_BA_TB.xlsx", None, ba, {"source": "demo generator"}, [], admin)
    store_tb(db, company, bud, "CC_TB", "demo_budget_CC_TB.xlsx", None, cc, {"source": "demo generator"}, [], admin)
    for name, values in drivers.items():
        for key, v in values.items():
            pl, g = key.split("|")
            db.add(DriverValue(company_id=company.id, period_id=bud.id, driver=name, plant_code=pl, pg_code=g,
                               value=v))
    audit.log(db, admin, "create", "company", company.id, company.id, demo=True)
    db.flush()
    return company


def seed_demo_runs(db: Session, company: Company, analyst: User | None, approver: User | None) -> None:
    """Calculate every demo period, add MIS figures and publish the half-year run."""
    from .runs import execute_run, transition
    from .runs import value_lookup
    periods = db.scalars(select(Period).where(Period.company_id == company.id).order_by(Period.end_date,
                                                                                        Period.id)).all()
    for p in periods:
        run = execute_run(db, company, p, analyst, note="Demo calculation")
        if p.scenario == "actual" and p.code == "2026-09":
            vals = value_lookup(db, run.id)
            mis = [("Sales", "[L:sales_total]", 0.0), ("Material cost", "[L:material_cost]", 0.0),
                   ("Factory overhead", "[L:factory_overhead]", 0.0), ("Corporate overheads",
                                                                       "[L:corporate_overheads]", 1.5e5),
                   ("PRE cost", "[L:pre_cost_total]", 0.0), ("PBT", "[L:pbt]", 1.5e5)]
            for i, (label, expr, delta) in enumerate(mis):
                line = expr[3:-1]
                v = vals.get((line, "__ALL__", "__TOTAL__"), 0.0)
                db.add(MisLine(company_id=company.id, period_id=p.id, label=label, expr=expr, mis_value=v + delta,
                               compare="difference", sort_order=i))
            db.commit()
            if analyst and approver:
                transition(db, run, "submit", analyst, "Demo: controls reviewed")
                transition(db, run, "approve", approver, "Demo approval")
                transition(db, run, "publish", approver)
