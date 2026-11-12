"""Unit tests for every calculation step, on a tiny synthetic company.

Company: plants P1, P2; product groups A, B; Common (COM) and Corporate (COR).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine import CostingEngine, EngineConfig, EngineError, EngineInputs, expr

CR = 1e7


def base_config(**over) -> EngineConfig:
    cfg = dict(
        plants=["P1", "P2"], product_groups=["A", "B"], calc_unit_divisor=CR,
        business_areas={
            "P1A": {"plant": "P1", "code": "A"}, "P1B": {"plant": "P1", "code": "B"},
            "P1C": {"plant": "P1", "code": "COM"}, "P1R": {"plant": "P1", "code": "COR"},
            "P2A": {"plant": "P2", "code": "A"}, "P2B": {"plant": "P2", "code": "B"},
            "P2C": {"plant": "P2", "code": "COM"}, "P2R": {"plant": "P2", "code": "COR"},
            "X9": {"plant": "OTHER", "code": "A"},
        },
        gl_accounts={
            "5000": {"category": "Sales", "type": "NONPRE"},
            "6000": {"category": "Power", "type": "NONPRE"},
            "6100": {"category": "power", "type": "NONPRE"},          # case-insensitive category match
            "6200": {"category": "Wages", "type": "PRE"},
            "9000": {"category": None, "type": "EXCLUDED"},
            "7777": {"category": "Unknown cat", "type": "NONPRE"},
        },
        cost_centres={
            "c1": {"plant": "P1", "function": "A"},
            "c2": {"plant": "P1", "function": "QC"},
            "c3": {"plant": "P2", "function": "COR"},
            "c4": {"plant": "P2", "function": "B"},
        },
        categories=[{"name": "Sales", "sign": -1}, {"name": "Power", "sign": 1}],
        pre_categories=["Wages"],
        functions={"QC": {"group": "factory", "mode": "plant"}, "COR": {"group": "corporate", "mode": "pooled"}},
        rules=[
            {"dataset": "NONPRE", "category": "Sales", "pool": "COM", "mode": "plant",
             "basis": {"kind": "component", "category": "Sales", "component": "direct"}},
            {"dataset": "NONPRE", "category": "Power", "pool": "COM", "mode": "plant",
             "basis": {"kind": "line", "line": "sales"}},
            {"dataset": "NONPRE", "category": "Power", "pool": "COR", "mode": "pooled",
             "basis": {"kind": "line", "line": "sales"}},
            {"dataset": "PRE", "category": "Wages", "pool": "QC", "mode": "plant",
             "basis": {"kind": "const", "value": 1.0}},
            {"dataset": "PRE", "category": "Wages", "pool": "COR", "mode": "pooled",
             "basis": {"kind": "line", "line": "sales"}},
        ],
        lines=[
            {"code": "sales", "label": "Sales", "expr": "[C:Sales|total]"},
            {"code": "power", "label": "Power & fuel", "expr": "[C:Power|total]"},
            {"code": "pre_fac", "label": "PRE factory", "expr": "[P:group|factory]"},
            {"code": "pre_corp", "label": "PRE corporate", "expr": "[P:group|corporate]"},
            {"code": "pbt", "label": "PBT", "expr": "[L:sales] - [L:power] - [L:pre_fac] - [L:pre_corp]"},
            {"code": "margin_pct", "label": "PBT %", "expr": "[L:pbt] / [L:sales]", "additive": False},
        ],
        meta={"pbt_line": "pbt", "sales_line": "sales"},
    )
    cfg.update(over)
    return EngineConfig(**cfg)


def tb(rows, dim):
    return pd.DataFrame(rows, columns=["gl", dim, "amount"])


def standard_inputs(**over) -> EngineInputs:
    ba = tb([
        ("5000", "P1A", -60 * CR), ("5000", "P1B", -40 * CR), ("5000", "P1C", -10 * CR),
        ("5000", "P2A", -50 * CR),
        ("6000", "P1A", 3 * CR), ("6000", "P1C", 5 * CR), ("6100", "P1C", 1 * CR),
        ("6000", "P2C", 2 * CR), ("6000", "P1R", 4 * CR), ("6000", "P2R", 2 * CR),
        ("6200", "P1A", 999 * CR),          # PRE GL in BA TB: must be ignored (taken from CC TB)
        ("9000", "P1A", 7 * CR),            # excluded GL
    ], "ba")
    cc = tb([("6200", "c1", 5 * CR), ("6200", "c2", 2 * CR), ("6200", "c3", 3 * CR),
             ("6200", "c4", 1 * CR), ("5000", "c1", -1 * CR)], "cc")
    kw = dict(ba_tb=ba, cc_tb=cc)
    kw.update(over)
    return EngineInputs(**kw)


def run(cfg=None, inputs=None):
    return CostingEngine(cfg or base_config(), inputs or standard_inputs()).run()


def g(res, line):
    return res.lines[line]


# ---------------------------------------------------------------- expression language
def test_expression_parse_and_evaluate():
    vals = {"a": np.array([1.0, 2.0]), "b": np.array([3.0, 4.0])}
    out = expr.evaluate("[L:a] * 2 - SUM([L:b], 1) / (2)", lambda r: vals[r.body])
    assert np.allclose(out, [1.0 * 2 - 4 / 2, 2 * 2 - 5 / 2])
    assert expr.evaluate("-(3) + 1", lambda r: 0) == -2
    assert expr.evaluate("[L:a] / 0", lambda r: vals[r.body]).tolist() == [0.0, 0.0]   # safe division
    assert [r.text for r in expr.refs(expr.parse("[C:Power & Fuel|total] + [MCADJ]"))] == \
        ["[C:Power & Fuel|total]", "[MCADJ]"]
    with pytest.raises(expr.ExpressionError):
        expr.parse("[L:a] +")
    with pytest.raises(expr.ExpressionError):
        expr.parse("os.system(1)")


# ---------------------------------------------------------------- tagging & aggregation
def test_tagging_statuses_and_signs():
    res = run()
    st = res.np_facts.groupby("status")["amount"].sum() / CR
    assert st["ok"] == pytest.approx(-160 + 3 + 5 + 1 + 2 + 4 + 2)
    assert st["pre_gl"] == pytest.approx(999)
    assert st["excluded_gl"] == pytest.approx(7)
    e = res.engine
    # Sales sign -1 => positive; unit = crore
    assert e.direct["Sales"][e.pidx["P1"], e.kidx["A"]] == pytest.approx(60)
    assert e.direct["Sales"][e.pidx["P1"], e.kidx["COM"]] == pytest.approx(10)
    # GL 6100 maps to "power" -> same category (case-insensitive)
    assert e.direct["Power"][e.pidx["P1"], e.kidx["COM"]] == pytest.approx(6)
    assert e.cor_memo["Power"].tolist() == pytest.approx([4, 2])


def test_unmapped_items_are_flagged_not_lost():
    ba = standard_inputs().ba_tb
    ba = pd.concat([ba, tb([("8888", "P1A", 1 * CR), ("6000", "NEWBA", 2 * CR), ("6000", "X9", 3 * CR),
                            ("7777", "P1A", 4 * CR)], "ba")])
    res = run(inputs=standard_inputs(ba_tb=ba))
    ctl = {c.code: c for c in res.controls}
    assert ctl["np_unmapped_gl"].amount == pytest.approx(1) and ctl["np_unmapped_gl"].level == "error"
    assert ctl["np_unmapped_ba"].amount == pytest.approx(2)
    assert ctl["np_outside_plants"].amount == pytest.approx(3)
    assert ctl["np_category_not_in_pnl"].amount == pytest.approx(4)
    assert ctl["tb_tieout"].level == "ok"   # still ties: every rupee is accounted for


# ---------------------------------------------------------------- adjustments
def test_adjustment_rules():
    adj = [
        {"type": "regroup_gl", "tb": "BA", "from_gls": ["6100"], "to_gl": "6000", "reason": "regroup"},
        {"type": "move_dim", "tb": "BA", "gl": "6000", "from": ["P2C"], "to": "P2A", "reason": "move"},
        {"type": "amount", "tb": "BA", "gl": "6000", "dim": "P1B", "amount": 0.5 * CR, "reason": "fix"},
        {"type": "exclude_gl", "tb": "BA", "gls": ["9000"], "reason": "exclude"},
        {"type": "reclass", "tb": "BA", "from": {"gl": "5000", "dim": "P1A"}, "to": {"dim": "P1B"},
         "amount": -10 * CR, "reason": "reclass"},
        {"type": "amount", "tb": "CC", "gl": "6200", "dim": "c4", "amount": -1 * CR, "reason": "zero c4"},
    ]
    res = run(inputs=standard_inputs(adjustments=adj))
    e = res.engine
    assert e.direct["Power"][e.pidx["P2"], e.kidx["A"]] == pytest.approx(2)        # moved from P2 COM
    assert e.direct["Power"][e.pidx["P2"], e.kidx["COM"]] == pytest.approx(0)
    assert e.direct["Power"][e.pidx["P1"], e.kidx["B"]] == pytest.approx(0.5)
    assert e.direct["Sales"][e.pidx["P1"], e.kidx["A"]] == pytest.approx(50)       # 60 - 10 reclassed
    assert e.direct["Sales"][e.pidx["P1"], e.kidx["B"]] == pytest.approx(50)
    assert e.pre_direct["Wages"][e.pidx["P2"], e.kidx["B"]] == pytest.approx(0)
    assert len(res.adjust_log) == 6 and res.adjust_log[0]["amount"] == pytest.approx(1 * CR)
    assert (res.np_facts["gl"] == "9000").sum() == 0


# ---------------------------------------------------------------- allocation
def test_common_allocation_within_plant_and_com_reversal():
    res = run()
    e = res.engine
    common = e.component("Sales", "common")
    # P1 COM sales 10 spread on direct sales 60:40
    assert common[e.pidx["P1"], e.kidx["A"]] == pytest.approx(6)
    assert common[e.pidx["P1"], e.kidx["B"]] == pytest.approx(4)
    assert common[e.pidx["P1"], e.kidx["COM"]] == pytest.approx(-10)      # reversal keeps plant total
    total = e.component("Sales", "total")
    assert total[e.pidx["P1"]].sum() == pytest.approx(110)                  # plant total unchanged
    assert total[e.pidx["P1"], e.kidx["COM"]] == pytest.approx(0)


def test_basis_on_post_allocation_line_and_corporate_pool():
    res = run()
    e = res.engine
    sales = g(res, "sales")
    assert sales[e.pidx["P1"], :2].tolist() == pytest.approx([66, 44])
    power_common = e.component("Power", "common")
    # P1 COM power 6 on post-allocation sales 66:44
    assert power_common[e.pidx["P1"], :2].tolist() == pytest.approx([6 * 0.6, 6 * 0.4])
    # P2 COM power 2: P2 sales are all in A
    assert power_common[e.pidx["P2"], :2].tolist() == pytest.approx([2, 0])
    corp = e.component("Power", "corporate")
    # COR pool 4 + 2 = 6 spread over all plants on sales 66, 44, 50, 0
    assert corp[:, :2].sum() == pytest.approx(6)
    assert corp[e.pidx["P2"], e.kidx["A"]] == pytest.approx(6 * 50 / 160)
    assert corp[:, e.kidx["COM"]].tolist() == [0, 0]


def test_allocation_in_equals_out_control_and_zero_basis():
    cfg = base_config()
    cfg.rules[1] = {"dataset": "NONPRE", "category": "Power", "pool": "COM", "mode": "plant",
                    "basis": {"kind": "const", "value": 0.0}}
    res = run(cfg)
    ctl = {c.code: c for c in res.controls}["allocation_balance"]
    assert ctl.level == "warning"
    assert ctl.amount == pytest.approx(6 + 2)          # both COM pools unallocated
    assert {d["status"] for d in ctl.details} == {"zero_or_invalid_basis"}


def test_custom_legs_denominators_plugs_and_fixed_cells():
    cfg = base_config()
    cfg.rules[1] = {
        "dataset": "NONPRE", "category": "Power", "pool": "COM", "mode": "custom",
        "basis": {"kind": "const", "value": 1.0},
        "legs": [
            {"sources": ["P1"], "targets": ["P1", "P2"], "denominator": {"kind": "basis", "plants": "all"}},
            {"sources": ["P2"], "targets": ["P2"], "denominator": {"kind": "const", "value": 4.0}},
        ],
        "plugs": {"P1|A": 0.25},
        "fixed_cells": {"P2|B": 9.0},
    }
    res = run(cfg)
    e = res.engine
    c = e.component("Power", "common")
    assert c[e.pidx["P1"], e.kidx["A"]] == pytest.approx(6 / 4 + 0.25)
    assert c[e.pidx["P2"], e.kidx["A"]] == pytest.approx(6 / 4 + 2 / 4)
    assert c[e.pidx["P2"], e.kidx["B"]] == pytest.approx(9.0)                   # fixed
    assert c[e.pidx["P1"], e.kidx["COM"]] == pytest.approx(-(6 / 4 + 0.25 + 6 / 4))


def test_basis_kinds_driver_sum_at_and_cells():
    cfg = base_config()
    cfg.rules[1] = {"dataset": "NONPRE", "category": "Power", "pool": "COM", "mode": "plant",
                    "basis": {"kind": "driver", "driver": "machine_hours"},
                    "basis_cells": {"P2|A": {"kind": "sum", "items": [{"kind": "const", "value": 1},
                                                                      {"kind": "line", "line": "sales",
                                                                       "at": "P1|A"}]}}}
    inputs = standard_inputs(drivers={"machine_hours": {"P1|A": 1, "P1|B": 3, "P2|B": 5}})
    res = run(cfg, inputs)
    e = res.engine
    c = e.component("Power", "common")
    assert c[e.pidx["P1"], :2].tolist() == pytest.approx([6 * 1 / 4, 6 * 3 / 4])
    assert c[e.pidx["P2"], :2].tolist() == pytest.approx([2 * 67 / 72, 2 * 5 / 72])


# ---------------------------------------------------------------- PRE
def test_pre_direct_plant_function_and_pooled_corporate():
    res = run()
    e = res.engine
    assert e.pre_direct["Wages"][e.pidx["P1"], e.kidx["A"]] == pytest.approx(5)
    assert e.pre_direct["Wages"][e.pidx["P2"], e.kidx["B"]] == pytest.approx(1)
    qc = e.pre_item("Wages", "QC")
    assert qc[e.pidx["P1"], :2].tolist() == pytest.approx([1, 1])          # equal basis within P1
    cor = e.pre_item("Wages", "COR")
    assert cor[:, :2].sum() == pytest.approx(3)                            # pooled over both plants
    assert g(res, "pre_corp").sum() == pytest.approx(3)
    assert g(res, "pre_fac").sum() == pytest.approx(5 + 1 + 2)
    assert {c.code: c for c in res.controls}["pre_cross_check"].amount == pytest.approx(11 - 999)


# ---------------------------------------------------------------- P&L & controls
def test_pnl_totals_ratio_and_coverage_controls():
    res = run()
    pbt_company = res.value("pbt")
    tb_profit = 160 - (3 + 5 + 1 + 2 + 4 + 2) - (5 + 2 + 3 + 1)
    assert pbt_company == pytest.approx(tb_profit)
    ctl = {c.code: c for c in res.controls}
    assert ctl["pbt_vs_tb"].level == "ok"
    assert ctl["pbt_coverage"].level == "ok"
    # ratio lines are re-evaluated on totals, not summed
    assert res.value("margin_pct") == pytest.approx(pbt_company / 160)
    assert res.value("margin_pct", "P1") == pytest.approx(res.value("pbt", "P1") / res.value("sales", "P1"))


def test_coverage_control_detects_double_count_and_wrong_sign():
    cfg = base_config()
    cfg.lines[4] = {"code": "pbt", "label": "PBT", "expr": "[L:sales] + [L:power] - [L:pre_fac] - [L:pre_corp]"}
    res = run(cfg)
    ctl = {c.code: c for c in res.controls}["pbt_coverage"]
    assert ctl.level == "warning"
    assert any(d["key"].startswith("Power") for d in ctl.details)


def test_plant_cell_and_total_overrides():
    cfg = base_config()
    cfg.lines.append({"code": "x", "label": "X", "expr": "[L:sales]", "plant_exprs": {"P2": "0"},
                      "cell_exprs": {"P1|B": "1"}, "total_exprs": {"P1": "100"}})
    res = run(cfg)
    assert res.value("x", "P1", "A") == pytest.approx(66)
    assert res.value("x", "P1", "B") == pytest.approx(1)
    assert res.value("x", "P2", "A") == pytest.approx(0)
    assert res.value("x", "P1") == pytest.approx(100)        # plant-total override
    assert res.value("x") == pytest.approx(100)              # company = sum of plant totals


def test_cycle_detection():
    cfg = base_config()
    cfg.lines.append({"code": "loop_a", "label": "a", "expr": "[L:loop_b]"})
    cfg.lines.append({"code": "loop_b", "label": "b", "expr": "[L:loop_a]"})
    with pytest.raises(EngineError, match="Circular"):
        run(cfg)


def test_mc_adjustments_amount_and_target_pct():
    cfg = base_config()
    cfg.lines.append({"code": "mc", "label": "MC", "expr": "[L:power] + [MCADJ]"})
    cfg.meta["mc_tb_line"] = "power"
    inputs = standard_inputs(mc_adjustments={"P1|A": 1.5, "P1|B": {"kind": "mc_pct", "value": 0.5}})
    res = run(cfg, inputs)
    assert res.value("mc", "P1", "A") == pytest.approx(res.value("power", "P1", "A") + 1.5)
    assert res.value("mc", "P1", "B") == pytest.approx(0.5 * res.value("sales", "P1", "B"))


def test_config_validation_reports_problems():
    cfg = base_config()
    cfg.lines.append({"code": "bad", "label": "Bad", "expr": "[L:nope] + [C:Power|weird]"})
    cfg.rules.append(dict(cfg.rules[0]))
    msgs = [p["message"] for p in cfg.validate() if p["level"] == "error"]
    assert any("unknown line" in m for m in msgs)
    assert any("Duplicate allocation rule" in m for m in msgs)
    assert any("unknown component" in m for m in msgs)


def test_long_table_and_drilldown():
    res = run()
    lt = res.long_table()
    tot = lt[(lt.line == "sales") & (lt.plant == "__ALL__") & (lt.code == "__TOTAL__")]["value"].iloc[0]
    assert tot == pytest.approx(160)
    parts = res.explain("pbt", "P1", "A")
    assert sum(p["value"] for p in parts) == pytest.approx(res.value("pbt", "P1", "A"))
    rows = res.source_rows("Power", "common", "P1", "A")
    assert rows["value"].sum() == pytest.approx(res.engine.component("Power", "common")[0, 0])


def test_drilldown_follows_overrides_pins_and_totals():
    cfg = base_config()
    cfg.lines.append({"code": "x", "label": "X", "expr": "[L:sales] + 2 * [L:power]", "plant_exprs": {"P2": "[L:power]"},
                      "cell_exprs": {"P1|B": "[L:sales@P2|A] - 1"}, "total_exprs": {"P1": "[L:sales] * 0.5"}})
    cfg.lines.append({"code": "y", "label": "Y", "expr": "[L:x] + [L:sales] * [L:power]"})   # non-linear term
    res = run(cfg)
    e = res.engine
    for line in ("x", "y"):
        for p in e.plants:
            for code in [None] + e.codes:
                parts = res.explain(line, p, code or "__TOTAL__")
                assert sum(x["value"] for x in parts) == pytest.approx(res.value(line, p, code), abs=1e-9), (line, p, code)
    pinned = res.explain("x", "P1", "B")
    assert any("taken from P2" in x["label"] for x in pinned)
    assert any(x["kind"] == "const" for x in pinned)
    assert any(x["kind"] == "X" for x in res.explain("y", "P1", "A"))


def test_performance_1000_gls_900_columns():
    """~1,000 GLs x ~900 business areas must calculate well under 30 seconds."""
    rng = np.random.default_rng(7)
    plants = [f"PL{i}" for i in range(6)]
    pgs = [f"G{i:02d}" for i in range(14)]
    bas = {}
    for p in plants:
        for code in pgs + ["COM", "COR"]:
            for k in range(9):
                bas[f"{p}_{code}_{k}"] = {"plant": p, "code": code}
    cats = [{"name": f"Cat{i}", "sign": -1 if i < 5 else 1} for i in range(80)]
    gls = {str(100000 + i): {"category": f"Cat{i % 80}", "type": "NONPRE"} for i in range(1000)}
    rules = []
    for c in cats:
        rules.append({"dataset": "NONPRE", "category": c["name"], "pool": "COM", "mode": "plant",
                      "basis": {"kind": "line", "line": "sales"}})
        rules.append({"dataset": "NONPRE", "category": c["name"], "pool": "COR", "mode": "pooled",
                      "basis": {"kind": "line", "line": "sales"}})
    rules[0] = {"dataset": "NONPRE", "category": "Cat0", "pool": "COM", "mode": "plant",
                "basis": {"kind": "component", "category": "Cat0", "component": "direct"}}
    rules[1] = {"dataset": "NONPRE", "category": "Cat0", "pool": "COR", "mode": "pooled",
                "basis": {"kind": "component", "category": "Cat0", "component": "direct"}}
    lines = [{"code": "sales", "label": "Sales", "expr": "[C:Cat0|total]"},
             {"code": "costs", "label": "Costs", "expr": " + ".join(f"[C:Cat{i}|total]" for i in range(5, 80))},
             {"code": "pbt", "label": "PBT", "expr": "[L:sales] + " + " + ".join(
                 f"[C:Cat{i}|total]" for i in range(1, 5)) + " - [L:costs]"}]
    cfg = EngineConfig(plants=plants, product_groups=pgs, business_areas=bas, gl_accounts=gls, categories=cats,
                       rules=rules, lines=lines, meta={"pbt_line": "pbt"})
    ba_names = list(bas)
    n = 60000
    df = pd.DataFrame({"gl": rng.choice(list(gls), n), "ba": rng.choice(ba_names, n),
                       "amount": rng.normal(0, 1e6, n)})
    df.loc[df["gl"] == "100000", "amount"] = -abs(df.loc[df["gl"] == "100000", "amount"]) - 1
    res = CostingEngine(cfg, EngineInputs(df, pd.DataFrame(columns=["gl", "cc", "amount"]))).run()
    assert res.timings["total"] < 30
    assert {c.code: c for c in res.controls}["tb_tieout"].level == "ok"
