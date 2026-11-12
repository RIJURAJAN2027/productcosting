"""Import corrections of legacy-workbook errors, on a small synthetic import.

Plants P1, P2; product groups A, B. The import has a fixed amount typed into the Material common
allocation, a fixed amount added to a report total, one GL whose category never reaches the P&L but
whose FS description matches other material GLs, and one GL that cannot be placed.
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import pandas as pd
import pytest
from openpyxl import load_workbook

from app.importers.corrections import (CorrectionOptions, apply_corrections, calculate, correction_effect,
                                       reconciliation_workbook, suggest_category)

CR = 1e7
PLUG_EXPR = "([L:material] + -0.25)"


def make_import():
    config = dict(
        plants=["P1", "P2"], product_groups=["A", "B"], calc_unit_divisor=CR,
        business_areas={"P1A": {"plant": "P1", "code": "A"}, "P1B": {"plant": "P1", "code": "B"},
                        "P1C": {"plant": "P1", "code": "COM"}, "P2A": {"plant": "P2", "code": "A"},
                        "P2B": {"plant": "P2", "code": "B"}, "P2C": {"plant": "P2", "code": "COM"}},
        gl_accounts={
            "5000": {"category": "Sales", "type": "NONPRE", "description": "Sales"},
            "6000": {"category": "Material", "type": "NONPRE", "description": "Raw material"},
            "6001": {"category": "Freight", "type": "NONPRE", "description": "Freight inward"},
            "6002": {"category": "Freight", "type": "NONPRE", "description": "Freight other"},
            "6900": {"category": "Material (old name)", "type": "NONPRE", "description": "Supplier credit"},
            "7000": {"category": "Odd", "type": "NONPRE", "description": "Unclassified"},
            "6200": {"category": "Wages", "type": "PRE", "description": "Wages"},
        },
        cost_centres={"c1": {"plant": "P1", "function": "A"}},
        categories=[{"name": "Sales", "sign": -1}, {"name": "Material", "sign": 1}, {"name": "Freight", "sign": 1}],
        pre_categories=["Wages"], functions={},
        rules=[
            {"dataset": "NONPRE", "category": "Sales", "pool": "COM", "mode": "plant", "label": "Sales - Common",
             "basis": {"kind": "component", "category": "Sales", "component": "direct"}},
            {"dataset": "NONPRE", "category": "Material", "pool": "COM", "mode": "plant",
             "label": "Material - Common", "source": "Realloc!12",
             "basis": {"kind": "component", "category": "Material", "component": "direct"},
             "plugs": {"P1|A": -0.5}},
            {"dataset": "NONPRE", "category": "Freight", "pool": "COM", "mode": "plant",
             "label": "Freight - Common", "basis": {"kind": "line", "line": "material"}},
        ],
        lines=[
            {"code": "sales", "label": "Sales", "expr": "[C:Sales|total]"},
            # as in the legacy workbook, the material line shows 0 in the common (COM) column, so a plug
            # inside the common allocation changes the plant total instead of only moving cost between PGs
            {"code": "material", "label": "Material - Total", "expr": "[C:Material|total]",
             "cell_exprs": {"P1|COM": "0", "P2|COM": "0"}},
            {"code": "freight", "label": "Freight - Total", "expr": "[C:Freight|total]"},
            {"code": "mc_total", "label": "Material cost - Total", "expr": "[L:material] + [L:freight]"},
            {"code": "pbt", "label": "PBT", "expr": "[L:sales] - [L:mc_total] - [P:direct]"},
            {"code": "rep_material", "label": "Material - Total (report)", "expr": "[L:material]",
             "total_exprs": {"P2": PLUG_EXPR}},
            {"code": "rep_sales", "label": "Sales (report)", "expr": "[L:sales]"},
        ],
        meta={"pbt_line": "pbt", "sales_line": "sales"},
    )
    ba = pd.DataFrame([
        ("5000", "P1A", -60 * CR), ("5000", "P1B", -40 * CR), ("5000", "P2A", -30 * CR), ("5000", "P2B", -20 * CR),
        ("6000", "P1A", 20 * CR), ("6000", "P1B", 10 * CR), ("6000", "P1C", 6 * CR),
        ("6000", "P2A", 8 * CR), ("6000", "P2B", 4 * CR), ("6000", "P2C", 3 * CR),
        ("6001", "P1A", 1 * CR), ("6001", "P1C", 0.4 * CR), ("6002", "P2B", 0.2 * CR),
        ("6900", "P1C", -1.0 * CR), ("6900", "P1A", 0.2 * CR),
        ("7000", "P2A", 0.05 * CR),
    ], columns=["gl", "ba", "amount"])
    cc = pd.DataFrame([("6200", "c1", 2 * CR)], columns=["gl", "cc", "amount"])
    fs = {"5000": "SALES", "6000": "MATERIALS", "6001": "MATERIALS", "6002": "MATERIALS", "6900": "MATERIALS",
          "7000": "SOMETHING ELSE", "6200": "EMPLOYEE COST"}
    masters = {"gl_accounts": {k: {**v, "fs_description": fs[k]} for k, v in config["gl_accounts"].items()}}
    trace = {"report_plugs": [{"cell": "Report P2!C10", "expr": PLUG_EXPR, "base": "[L:material]",
                               "amount": -0.25}]}
    return SimpleNamespace(config=config, ba_tb=ba, cc_tb=cc, adjustments=[], mc_adjustments={},
                           masters=masters, trace=trace)


def test_suggest_category_uses_fs_description_by_amount_then_count():
    res = make_import()
    tb = res.ba_tb.groupby("gl")["amount"].sum().to_dict()
    pnl = {c["name"].lower(): c["name"] for c in res.config["categories"]}
    # Freight has two GLs, Material one GL with by far the larger amount -> Material
    assert suggest_category("6900", res.masters["gl_accounts"], pnl, tb)[0] == "Material"
    target, reason = suggest_category("7000", res.masters["gl_accounts"], pnl, tb)
    assert target is None and "no other GL" in reason


def test_corrections_remove_plugs_map_gls_and_report_their_effect():
    res = make_import()
    before = calculate(res)
    cb = {c.code: c for c in before.controls}
    assert cb["allocation_plugs"].amount == pytest.approx(-0.5)
    assert cb["np_category_not_in_pnl"].amount == pytest.approx(-0.8 + 0.05)

    changes = apply_corrections(res, CorrectionOptions())
    kinds = [c["kind"] for c in changes]
    assert kinds.count("allocation_plug_removed") == 1 and kinds.count("report_plug_removed") == 1
    plug = next(c for c in changes if c["kind"] == "allocation_plug_removed")
    assert plug["amount"] == pytest.approx(-0.5) and "P1|A" in plug["where"] and plug["source"] == "Realloc!12"
    rep = next(c for c in changes if c["kind"] == "report_plug_removed")
    assert rep["source"] == "Report P2!C10" and "P2" in rep["where"]
    remap = next(c for c in changes if c["kind"] == "gl_remapped")
    assert (remap["gl"], remap["category"], remap["to"]) == ("6900", "Material (old name)", "Material")
    assert remap["amount"] == pytest.approx(-0.8)
    odd = next(c for c in changes if c["kind"] == "gl_not_mapped")
    assert odd["gl"] == "7000" and odd["amount"] == pytest.approx(0.05)

    assert not any("plugs" in r for r in res.config["rules"])
    rep_line = next(ln for ln in res.config["lines"] if ln["code"] == "rep_material")
    assert rep_line["total_exprs"]["P2"] == "[L:material]"
    assert res.config["gl_accounts"]["6900"]["category"] == "Material"
    assert "corrected on import" in res.masters["gl_accounts"]["6900"]["note"]

    after = calculate(res)
    ca = {c.code: c for c in after.controls}
    assert "allocation_plugs" not in ca
    assert ca["np_category_not_in_pnl"].amount == pytest.approx(0.05)     # only the GL that could not be placed
    # removing the plug adds 0.5 of material cost; the supplier credit of 0.8 now reduces it
    assert after.value("pbt") - before.value("pbt") == pytest.approx(-0.5 + 0.8)
    assert after.value("material", "P1") - before.value("material", "P1") == pytest.approx(0.5 - 0.8)
    # the report total shows the calculated value again
    assert before.value("rep_material", "P2") == pytest.approx(before.value("material", "P2") - 0.25)
    assert after.value("rep_material", "P2") == pytest.approx(after.value("material", "P2"))

    effect = correction_effect(before, after, "pbt")
    company = next(r for r in effect["pbt"] if r["plant"] == "__ALL__")
    assert company["change"] == pytest.approx(0.3)
    assert effect["unit"] == "crore" and effect["values_changed"] > 0
    assert {"PBT", "Material - Total", "Material - Total (report)"} <= set(effect["lines_changed"])
    assert "Sales (report)" not in effect["lines_changed"]
    plugs_ctl = next(c for c in effect["controls"] if c["code"] == "allocation_plugs")
    assert plugs_ctl["before"] == pytest.approx(-0.5) and plugs_ctl["after"] == 0.0

    wb = load_workbook(io.BytesIO(reconciliation_workbook(changes, effect)))
    assert wb.sheetnames == ["Summary", "Corrections", "Changed values"]
    assert wb["Corrections"].max_row == len(changes) + 1
    assert wb["Changed values"].max_row == effect["values_changed"] + 1


def test_corrections_can_be_switched_off_or_given_explicitly():
    res = make_import()
    changes = apply_corrections(res, CorrectionOptions(remove_plugs=False, map_unused_categories=False,
                                                       category_map={"Odd": "Freight"}))
    assert [(c["kind"], c.get("gl"), c.get("to")) for c in changes] == [("gl_remapped", "7000", "Freight")]
    assert res.config["rules"][1]["plugs"] == {"P1|A": -0.5}
    assert res.config["gl_accounts"]["6900"]["category"] == "Material (old name)"
    assert not CorrectionOptions(remove_plugs=False, map_unused_categories=False).any
