"""Corrections of legacy-workbook errors, applied to an imported configuration.

The importer first reproduces the workbook exactly (verified value by value). Known kinds of
workbook error can then be corrected; every correction is recorded, so the new baseline can be
reviewed and signed off:

* **allocation plugs** – fixed amounts typed into allocation formulas – are removed, so each pool
  is shared out by its basis only;
* **report plugs** – fixed amounts added to looked-up values in the report sheets – are removed;
* **GLs whose category never reaches the P&L** (the category has no row in the workbook's
  reallocation sheet) are mapped to the P&L category that the other GLs with the same FS
  description use – by amount, then by count – or to a category given explicitly.
"""
from __future__ import annotations

import copy
import io
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..engine import CostingEngine, EngineConfig, EngineInputs
from ..engine.config import NONPRE, norm
from ..engine.core import EngineResult

ALL, TOTAL = "__ALL__", "__TOTAL__"
UNIT_NAMES = {1.0: "rupees", 1e3: "thousand", 1e5: "lakh", 1e6: "million", 1e7: "crore"}
# controls whose before / after amounts are shown with the corrections
CONTROL_CODES = ("np_category_not_in_pnl", "allocation_plugs", "allocation_balance", "pbt_vs_tb", "tb_tieout")


@dataclass
class CorrectionOptions:
    remove_plugs: bool = True
    map_unused_categories: bool = True
    category_map: dict[str, str] = field(default_factory=dict)   # GL code or category name -> P&L category

    @property
    def any(self) -> bool:
        return self.remove_plugs or self.map_unused_categories or bool(self.category_map)


def calculate(res) -> EngineResult:
    """Run the engine on an imported workbook (its own trial balances and inputs)."""
    cfg = EngineConfig.from_dict(copy.deepcopy(res.config))
    return CostingEngine(cfg, EngineInputs(res.ba_tb, res.cc_tb, res.adjustments, {}, res.mc_adjustments)).run()


def suggest_category(gl: str, gl_master: dict[str, dict], pnl_categories: dict[str, str],
                     tb: dict[str, float]) -> tuple[str | None, str]:
    """P&L category for a GL whose own category is not in the P&L: the category used by the other
    Non-PRE GLs with the same FS description (largest absolute TB amount, then most GLs)."""
    fs = str((gl_master.get(gl) or {}).get("fs_description") or "").strip()
    if not fs:
        return None, "the GL has no FS description to suggest a category"
    amount: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    for code, other in gl_master.items():
        if code == gl or str(other.get("fs_description") or "").strip() != fs or other.get("type") != NONPRE:
            continue
        cat = pnl_categories.get(norm(other.get("category")))
        if cat is None:
            continue
        amount[cat] += abs(float(tb.get(code, 0.0)))
        count[cat] += 1
    if not count:
        return None, f"no other GL with FS description {fs!r} has a P&L category"
    best = max(count, key=lambda c: (amount[c], count[c]))
    return best, (f"FS description {fs!r}: {count[best]} of the {sum(count.values())} other GLs with this "
                  f"description, and the largest amount, are in {best!r}")


def apply_corrections(res, options: CorrectionOptions) -> list[dict]:
    """Correct ``res`` (a LegacyImport) in place and return one record per change."""
    cfg = res.config
    div = float(cfg.get("calc_unit_divisor") or 1e7)
    changes: list[dict] = []

    def record(kind: str, where: str, amount: float, detail: str, **extra: Any) -> None:
        changes.append({"kind": kind, "where": where, "amount": float(amount), "amount_rupees": float(amount) * div,
                        "detail": detail, **extra})

    if options.remove_plugs:
        for rule in cfg.get("rules", []):
            for cell, amount in (rule.pop("plugs", None) or {}).items():
                record("allocation_plug_removed", f"{rule.get('label') or rule['category']} · {cell}", amount,
                       "Fixed amount inside the allocation formula removed; the pool is shared by its basis only.",
                       source=rule.get("source"))
        for plug in (res.trace or {}).get("report_plugs", []):
            hits = []
            for ln in cfg.get("lines", []):
                if plug["expr"] in (ln.get("expr") or ""):
                    ln["expr"] = ln["expr"].replace(plug["expr"], plug["base"])
                    hits.append(ln["label"])
                for attr in ("plant_exprs", "cell_exprs", "total_exprs"):
                    for key, text in list((ln.get(attr) or {}).items()):
                        if plug["expr"] in text:
                            ln[attr][key] = text.replace(plug["expr"], plug["base"])
                            hits.append(f"{ln['label']} · {key}{' total' if attr == 'total_exprs' else ''}")
            record("report_plug_removed", "; ".join(dict.fromkeys(hits)) or "(formula no longer present)",
                   plug["amount"], "Fixed amount added to a report figure removed; the report shows the calculated "
                                   "value.", source=plug["cell"])

    if options.map_unused_categories or options.category_map:
        pnl = {norm(c["name"]): c["name"] for c in cfg.get("categories", [])}
        tb = res.ba_tb.groupby("gl")["amount"].sum().to_dict() if res.ba_tb is not None and len(res.ba_tb) else {}
        gl_master = (res.masters or {}).get("gl_accounts", {})
        for code, info in cfg.get("gl_accounts", {}).items():
            cat = info.get("category")
            if info.get("type") != NONPRE or not cat or norm(cat) in pnl:
                continue
            desc = str(info.get("description") or (gl_master.get(code) or {}).get("description") or "")
            amount = float(tb.get(code, 0.0)) / div
            wanted = options.category_map.get(code) or options.category_map.get(cat)
            if wanted:
                target = pnl.get(norm(wanted))
                reason = f"mapped to {wanted!r} as requested" if target else f"{wanted!r} is not a P&L category"
            elif options.map_unused_categories:
                target, reason = suggest_category(code, gl_master, pnl, tb)
            else:
                continue
            if target is None:
                record("gl_not_mapped", f"GL {code} {desc}".strip(), amount,
                       f"Category {cat!r} is not in the P&L and {reason}; choose a category in Masters → GL "
                       "accounts.", gl=code, category=cat)
                continue
            info["category"] = target
            if code in gl_master:
                gl_master[code]["category"] = target
                gl_master[code]["note"] = (f"Category corrected on import from {cat!r}, which is not in the P&L, "
                                           f"to {target!r} – {reason}.")
            record("gl_remapped", f"GL {code} {desc}".strip(), amount,
                   f"Category {cat!r} has no row in the P&L, so the amount never reached it. Mapped to {target!r}: "
                   f"{reason}.", gl=code, category=cat, to=target)
    return changes


def _pbt_rows(result: EngineResult, pbt_line: str | None) -> list[tuple[str, float]]:
    if not pbt_line or pbt_line not in result.engine.lines:
        return []
    return [(p, result.value(pbt_line, p)) for p in result.plants] + [(ALL, result.value(pbt_line))]


def correction_effect(before: EngineResult, after: EngineResult, pbt_line: str | None) -> dict:
    """What the corrections changed (calculation units): every visible report value before / after,
    profit before tax by plant and the controls before / after."""
    rows = changed_values(before, after)
    pb, pa = dict(_pbt_rows(before, pbt_line)), dict(_pbt_rows(after, pbt_line))
    cb = {c.code: c for c in before.controls}
    ca = {c.code: c for c in after.controls}
    controls = []
    for code in CONTROL_CODES:
        if code in cb or code in ca:
            ref = ca.get(code) or cb.get(code)
            controls.append({"code": code, "title": ref.title,
                             "before": cb[code].amount if code in cb else 0.0,
                             "before_level": cb[code].level if code in cb else "ok",
                             "after": ca[code].amount if code in ca else 0.0,
                             "after_level": ca[code].level if code in ca else "ok"})
    return {
        "unit": UNIT_NAMES.get(float(after.engine.div), f"/ {after.engine.div:g}"),
        "values_changed": len(rows),
        "lines_changed": sorted({r["label"] for r in rows}),
        "pbt": [{"plant": p, "before": pb[p], "after": pa.get(p, 0.0), "change": pa.get(p, 0.0) - pb[p]}
                for p in pb],
        "controls": controls,
        "changed_values": rows,
    }


def changed_values(before: EngineResult, after: EngineResult, tol: float = 1e-9) -> list[dict]:
    """Every visible P&L value that the corrections changed: plant x PG cells and plant totals."""
    e = after.engine
    out, seen = [], set()
    for code, ln in e.lines.items():
        if ln.get("hidden") or code not in before.engine.lines:
            continue
        for p in after.plants:
            for g in list(after.codes) + [None]:
                b, a = before.value(code, p, g), after.value(code, p, g)
                if abs(a - b) <= tol:
                    continue
                key = (ln["label"], p, g, round(b, 9), round(a, 9))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"line": code, "label": ln["label"], "plant": p, "pg": g or TOTAL,
                            "before": b, "after": a, "change": a - b})
    return out


def reconciliation_workbook(changes: list[dict], effect: dict) -> bytes:
    """Excel workbook for signing off the corrected baseline."""
    rows = effect.get("changed_values", [])
    unit = effect.get("unit", "crore")
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    head = PatternFill("solid", fgColor="1F3A5F")
    hfont = Font(bold=True, color="FFFFFF")
    nf = "#,##0.000000;[Red]-#,##0.000000"
    wb = Workbook()

    def table(ws, start: int, header: list[str], data: list[list], widths: list[int]) -> None:
        for j, h in enumerate(header, 1):
            c = ws.cell(start, j, h)
            c.fill, c.font = head, hfont
        for i, row in enumerate(data, start + 1):
            for j, v in enumerate(row, 1):
                c = ws.cell(i, j, v)
                if isinstance(v, float):
                    c.number_format = nf
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + j)].width = w

    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Corrected baseline – reconciliation with the legacy workbook"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = (f"Amounts in {unit}. 'Before' is the workbook as imported (verified against the workbook); "
                "'after' is the result with the corrections listed on the next sheet.")
    ws["A2"].alignment = Alignment(wrap_text=False)
    table(ws, 4, ["Profit before tax", "Before", "After", "Change"],
          [["Company" if r["plant"] == ALL else r["plant"], r["before"], r["after"], r["change"]]
           for r in effect.get("pbt", [])], [34, 18, 18, 18])
    n = 6 + len(effect.get("pbt", []))
    table(ws, n, ["Control", "Before", "After", "Status after"],
          [[c["title"], c["before"], c["after"], c["after_level"].upper()] for c in effect.get("controls", [])],
          [34, 18, 18, 18])
    n += 2 + len(effect.get("controls", []))
    ws.cell(n, 1, f"Report values changed by the corrections: {effect.get('values_changed', 0):,} in "
                  f"{len(effect.get('lines_changed', []))} lines (see 'Changed values').")
    ws2 = wb.create_sheet("Corrections")
    table(ws2, 1, ["Correction", "Where", "Workbook cell", "Amount", "Explanation"],
          [[c["kind"].replace("_", " "), c["where"], c.get("source") or "", c["amount"], c["detail"]]
           for c in changes], [24, 60, 26, 16, 100])
    ws3 = wb.create_sheet("Changed values")
    table(ws3, 1, ["Line", "Plant", "Product group", "Before", "After", "Change"],
          [[r["label"], r["plant"], "Plant total" if r["pg"] == TOTAL else r["pg"], r["before"], r["after"],
            r["change"]] for r in rows], [44, 10, 16, 18, 18, 18])
    ws3.auto_filter.ref = f"A1:F{len(rows) + 1}"
    ws3.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
