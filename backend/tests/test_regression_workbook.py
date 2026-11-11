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
                                  "start_date": "2026-04-01"}, files={"file": ("wb.xlsx", f.read())})
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
