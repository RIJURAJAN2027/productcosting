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
