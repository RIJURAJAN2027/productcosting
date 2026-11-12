"""Regression test: the engine must reproduce the legacy Excel workbook to within Rs 1.

The workbook contains confidential company data, so it is never committed.  Point
the test at a local copy::

    PC_LEGACY_WORKBOOK=/path/to/Product_costing.xlsx pytest tests/test_regression_workbook.py

Without the variable the test is skipped.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.engine import CostingEngine, EngineConfig, EngineInputs
from app.engine.config import norm
from app.importers.corrections import CorrectionOptions, apply_corrections, calculate, changed_values
from app.importers.legacy_reference import compare_with_workbook
from app.importers.legacy_workbook import LegacyWorkbookImporter

WORKBOOK = os.environ.get("PC_LEGACY_WORKBOOK")

pytestmark = pytest.mark.skipif(not WORKBOOK or not Path(WORKBOOK).exists(),
                                reason="set PC_LEGACY_WORKBOOK to a legacy workbook to run the regression")


@pytest.fixture(scope="module")
def imported():
    imp = LegacyWorkbookImporter(WORKBOOK)
    res = imp.run()
    cfg = EngineConfig.from_dict(res.config)
    result = CostingEngine(cfg, EngineInputs(res.ba_tb, res.cc_tb, res.adjustments, {}, res.mc_adjustments)).run()
    return imp, res, result


def test_config_is_valid(imported):
    _, res, _ = imported
    problems = [p for p in EngineConfig.from_dict(res.config).validate() if p["level"] == "error"]
    assert problems == []


@pytest.mark.parametrize("section", ["components", "cor", "pre", "dfr", "reports"])
def test_matches_workbook_within_one_rupee(imported, section):
    imp, _, result = imported
    cmp = compare_with_workbook(imp, result, tolerance_rupees=1.0, sections=(section,))
    assert len(cmp.table) > 0, f"nothing compared in section {section}"
    mism = cmp.mismatches
    assert mism.empty, (f"{len(mism)} cell(s) differ by more than Rs 1 in {section}:\n"
                        + mism.head(20).to_string())


def test_full_calculation_is_fast(imported):
    _, _, result = imported
    assert result.timings["total"] < 30.0


def test_controls_flag_known_workbook_defects(imported):
    """The controls must surface the defects found in the legacy workbook."""
    _, _, result = imported
    by_code = {c.code: c for c in result.controls}
    assert by_code["tb_tieout"].level == "ok"                      # nothing silently lost
    assert by_code["np_unmapped_gl"].level == "ok"
    assert by_code["pre_cross_check"].level == "ok"                # CC-TB PRE == BA-TB PRE
    assert by_code["pbt_coverage"].level == "ok"                   # every category reaches PBT once
    # amounts the workbook drops / plugs are reported, not hidden
    assert by_code["np_category_not_in_pnl"].level == "error"
    assert "allocation_plugs" in by_code


@pytest.fixture(scope="module")
def corrected(imported):
    import copy
    _, res, before = imported
    fixed = copy.deepcopy(res)
    changes = apply_corrections(fixed, CorrectionOptions())
    return res, fixed, changes, before, calculate(fixed)


def test_corrections_remove_plugs_and_bring_every_gl_into_the_pnl(corrected):
    _, fixed, changes, before, after = corrected
    cb = {c.code: c for c in before.controls}
    ca = {c.code: c for c in after.controls}
    plugs = [c for c in changes if c["kind"] == "allocation_plug_removed"]
    assert sum(c["amount"] for c in plugs) == pytest.approx(
        cb["allocation_plugs"].amount if "allocation_plugs" in cb else 0.0, abs=1e-9)
    assert "allocation_plugs" not in ca and not any(r.get("plugs") for r in fixed.config["rules"])
    assert all(c["where"] != "(formula no longer present)" for c in changes if c["kind"] == "report_plug_removed")
    placed = [c for c in changes if c["kind"] == "gl_remapped"]
    unplaced = [c for c in changes if c["kind"] == "gl_not_mapped"]
    lost = cb["np_category_not_in_pnl"].amount if "np_category_not_in_pnl" in cb else 0.0
    assert sum(c["amount"] for c in placed + unplaced) == pytest.approx(lost, abs=1e-6)
    left = ca["np_category_not_in_pnl"].amount if "np_category_not_in_pnl" in ca else 0.0
    assert left == pytest.approx(sum(c["amount"] for c in unplaced), abs=1e-6)
    # the P&L now reconciles with the trial balance except for pools that still have no allocation
    remaining = ca["allocation_balance"].amount if "allocation_balance" in ca else 0.0
    assert ca["pbt_vs_tb"].amount == pytest.approx(remaining, abs=1e-6)


def _dependencies(cfg: dict) -> tuple[dict, dict]:
    """Line -> the lines / categories its formulas use; category -> what its allocation bases use."""
    import re
    from collections import defaultdict

    ref = re.compile(r"\[(L|C|P):([^\]]+)\]")
    line_refs = {}
    for ln in cfg["lines"]:
        texts = [ln.get("expr") or ""] + [t for a in ("plant_exprs", "cell_exprs", "total_exprs")
                                          for t in (ln.get(a) or {}).values()]
        refs = set()
        for text in texts:
            for kind, body in ref.findall(text):
                base = body.split("@")[0]
                refs.add(("L", base) if kind == "L" else ("C", norm(base.split("|")[0])) if kind == "C"
                         else ("P", ""))
        line_refs[ln["code"]] = refs
    rule_refs: dict = defaultdict(set)

    def walk(obj, acc):
        if isinstance(obj, dict):
            if obj.get("kind") in ("line", "line_total") and obj.get("line"):
                acc.add(("L", obj["line"]))
            if obj.get("kind") == "component" and obj.get("category"):
                acc.add(("C", norm(obj["category"])))
            if obj.get("kind") == "pre":
                acc.add(("P", ""))
            for v in obj.values():
                walk(v, acc)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, acc)

    for rule in cfg["rules"]:
        node = ("P", "") if rule.get("dataset") == "PRE" else ("C", norm(rule["category"]))
        walk(rule, rule_refs[node])
    return line_refs, rule_refs


def test_corrections_change_only_what_depends_on_them(corrected):
    """Every report value that changes belongs to a line that uses a corrected category, a
    corrected report formula, or an allocation whose basis depends on one of them."""
    import json
    res, fixed, changes, before, after = corrected
    line_refs, rule_refs = _dependencies(fixed.config)
    affected = {("C", norm(r["category"])) for r in res.config["rules"] if r.get("plugs")}
    affected |= {("C", norm(c["to"])) for c in changes if c["kind"] == "gl_remapped"}
    plugs = (res.trace or {}).get("report_plugs", [])
    affected |= {("L", ln["code"]) for ln in res.config["lines"] if any(p["expr"] in json.dumps(ln) for p in plugs)}
    grew = True
    while grew:
        grew = False
        for node, refs in list(line_refs.items()) + list(rule_refs.items()):
            key = ("L", node) if isinstance(node, str) else node
            if key not in affected and refs & affected:
                affected.add(key)
                grew = True
    changed = {r["line"] for r in changed_values(before, after)}
    assert changed, "the corrections should change the report"
    unexpected = changed - {name for kind, name in affected if kind == "L"}
    assert not unexpected, f"values changed in lines that do not depend on the corrections: {sorted(unexpected)}"


def test_legacy_import_through_api_and_database_roundtrip(tmp_path):
    """Import via the API, store masters/rules/lines/TBs in the database, rebuild the engine
    configuration from the database, calculate a run and compare it with the workbook again."""
    import os as _os
    from fastapi.testclient import TestClient

    old_url = _os.environ.get("PC_DATABASE_URL")
    _os.environ["PC_DATABASE_URL"] = f"sqlite+pysqlite:///{tmp_path / 'rt.db'}"
    from app.db import SessionLocal, reset_engine
    from app.main import create_app
    from app.models import CalcRun, Company, Period
    from app.services.runs import load_result
    from app.settings import get_settings

    get_settings.cache_clear()
    reset_engine()
    with TestClient(create_app()) as client:
        s = get_settings()
        tok = client.post("/api/auth/login", json={"email": s.bootstrap_admin_email,
                                                   "password": s.bootstrap_admin_password}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        cid = client.post("/api/companies", headers=h, json={"code": "LEGACY", "name": "Legacy import"}).json()["id"]
        with open(WORKBOOK, "rb") as f:
            r = client.post(f"/api/companies/{cid}/legacy-import", headers=h,
                            data={"period_code": "YTD", "period_label": "Legacy YTD", "end_date": "2026-09-30",
                                  "start_date": "2026-04-01", "remove_plugs": "false",
                                  "map_unused_categories": "false"},
                            files={"file": ("wb.xlsx", f.read())})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["regression"]["mismatches"] == 0
        run = client.post(f"/api/companies/{cid}/periods/{out['period_id']}/runs", headers=h, json={})
        assert run.status_code == 201, run.text
        db = SessionLocal()
        result = load_result(db, db.get(CalcRun, run.json()["id"]))
        db.close()
    imp = LegacyWorkbookImporter(WORKBOOK)
    imp.run()
    cmp = compare_with_workbook(imp, result, tolerance_rupees=1.0)
    if old_url is not None:
        _os.environ["PC_DATABASE_URL"] = old_url
    get_settings.cache_clear()
    reset_engine()
    assert cmp.mismatches.empty, cmp.mismatches.head(20).to_string()


def test_legacy_import_with_corrections_through_api_and_database(tmp_path, corrected):
    """The default import corrects the workbook errors, stores the corrected configuration, and a
    run calculated from the database gives exactly the corrected values."""
    import os as _os
    from fastapi.testclient import TestClient

    _, _, _, _, expected = corrected
    old_url = _os.environ.get("PC_DATABASE_URL")
    _os.environ["PC_DATABASE_URL"] = f"sqlite+pysqlite:///{tmp_path / 'fixed.db'}"
    from app.db import SessionLocal, reset_engine
    from app.main import create_app
    from app.models import CalcRun
    from app.services.runs import load_result
    from app.settings import get_settings

    get_settings.cache_clear()
    reset_engine()
    try:
        with TestClient(create_app()) as client:
            s = get_settings()
            tok = client.post("/api/auth/login", json={"email": s.bootstrap_admin_email,
                                                       "password": s.bootstrap_admin_password}).json()["access_token"]
            h = {"Authorization": f"Bearer {tok}"}
            cid = client.post("/api/companies", headers=h, json={"code": "FIXED", "name": "Corrected"}).json()["id"]
            with open(WORKBOOK, "rb") as f:
                r = client.post(f"/api/companies/{cid}/legacy-import", headers=h,
                                data={"period_code": "YTD", "end_date": "2026-09-30", "start_date": "2026-04-01"},
                                files={"file": ("wb.xlsx", f.read())})
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["regression"]["mismatches"] == 0          # the import itself still matches the workbook
            changes = out["corrections"]["changes"]
            assert changes and out["corrections"]["effect"]["values_changed"] > 0
            gls = {g["code"]: g for g in client.get(f"/api/companies/{cid}/masters/gl-accounts", headers=h).json()}
            for c in changes:
                if c["kind"] == "gl_remapped":
                    assert gls[c["gl"]]["category"] == c["to"]
                    assert "corrected on import" in (gls[c["gl"]]["note"] or "")
            rules = client.get(f"/api/companies/{cid}/masters/allocation-rules", headers=h).json()
            assert not any((r_.get("definition") or {}).get("plugs") for r_ in rules)
            run = client.post(f"/api/companies/{cid}/periods/{out['period_id']}/runs", headers=h, json={})
            assert run.status_code == 201, run.text
            controls = {c["code"]: c for c in run.json()["controls"]}
            assert "allocation_plugs" not in controls
            assert controls["np_category_not_in_pnl"]["level"] == "ok"
            db = SessionLocal()
            result = load_result(db, db.get(CalcRun, run.json()["id"]))
            db.close()
    finally:
        if old_url is not None:
            _os.environ["PC_DATABASE_URL"] = old_url
        get_settings.cache_clear()
        reset_engine()
    worst = 0.0
    for code, ln in expected.engine.lines.items():
        if ln.get("hidden"):
            continue
        for p in expected.plants:
            for g in list(expected.codes) + [None]:
                worst = max(worst, abs(result.value(code, p, g) - expected.value(code, p, g)))
    assert worst < 1e-9, f"database round trip differs from the corrected calculation by {worst}"
