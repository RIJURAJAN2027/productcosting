"""Compare engine results with the values cached in a legacy workbook.

Used by the regression test and by the "legacy import" screen to prove that an
imported configuration reproduces the workbook (to within Rs 1 by default).

Known, documented presentation differences are excluded and listed separately:

* detail lines that the workbook leaves blank for some plants (e.g. material-cost
  components shown only as a worked-out total) – the engine shows the details;
* the "Worked out MC" line, which the engine models as the *adjustment* on top of
  the TB material cost (the total material cost line itself is compared);
* the company-total block of "Data for reports" for lines whose sign the workbook
  flips only in that block (reports correct it, so report values are compared).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..engine.config import norm
from ..engine.core import EngineResult
from .legacy_workbook import ALL, TOTAL, LegacyWorkbookImporter


@dataclass
class Comparison:
    table: pd.DataFrame          # section, sheet, row, label, entity, expected, actual, abs_diff
    tolerance: float
    skipped: list[str] = field(default_factory=list)

    @property
    def mismatches(self) -> pd.DataFrame:
        return self.table[self.table["abs_diff"] > self.tolerance]

    def summary(self) -> pd.DataFrame:
        t = self.table
        g = t.groupby("section").agg(cells=("abs_diff", "size"),
                                     mismatches=("abs_diff", lambda s: int((s > self.tolerance).sum())),
                                     max_abs_diff=("abs_diff", "max"))
        return g.reset_index()


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return 0.0
    return None


def compare_with_workbook(imp: LegacyWorkbookImporter, result: EngineResult, tolerance_rupees: float = 1.0,
                          sections: tuple[str, ...] = ("components", "cor", "pre", "dfr", "reports")
                          ) -> Comparison:
    e = result.engine
    tol = tolerance_rupees / e.div
    rows: list[tuple] = []
    skipped: list[str] = []
    lines = e.lines

    def blank_plants(code: str) -> set[str]:
        return {p for (p, g) in imp.blank_cells.get(code, set())}

    import re as _re

    def alias(code: str, plant: str | None) -> str:
        """Follow pure aliases ([L:x]) to the line that actually holds the values."""
        seen = set()
        while code in lines and code not in seen:
            seen.add(code)
            ln = lines[code]
            text = ((ln.get("total_exprs") or {}).get(plant) if plant else None) or \
                ((ln.get("plant_exprs") or {}).get(plant) if plant else None) or ln.get("expr") or ""
            m = _re.fullmatch(r"\[L:([^@\]]+)\]", text.strip())
            if not m:
                break
            code = m.group(1)
        return code

    def comparable(code: str, plant: str | None) -> bool:
        plants = [plant] if plant else list(e.pidx)
        for p in plants:
            target = alias(code, p)
            if lines.get(target, {}).get("expr") == "[MCADJ]":
                return False
            if p in blank_plants(target):
                return False
        return True

    def add(section, sheet, r, label, ent, expected, actual):
        if expected is None:
            return
        rows.append((section, sheet, r, label, f"{ent[0]}|{ent[1]}", expected, actual, abs(expected - actual)))

    # --- Reallocation_non-PRE: direct / common / corporate / total components
    s = imp.sheet["realloc"]
    L = imp.layout(s)
    if "components" in sections:
        for r, d in imp.row_desc.items():
            if d[0] != "C" or d[1] not in e.direct:
                continue
            g = e.component(d[1], d[2])
            for c, ent in L.entity.items():
                if ent[0] == ALL or ent[1] == TOTAL:
                    continue
                add("components", s, r, f"{d[1]} [{d[2]}]", ent, _num(imp.V(s, r, c)),
                    float(g[e.pidx[ent[0]], e.kidx[ent[1]]]))
    if "cor" in sections:
        for r, d in imp.row_desc.items():
            if d[0] != "C" or d[2] != "direct" or d[1] not in e.cor_memo:
                continue
            for c, plant in imp.realloc_cor_cols.items():
                if plant in e.pidx:
                    add("cor", s, r, f"{d[1]} [COR]", (plant, "COR"), _num(imp.V(s, r, c)),
                        float(e.cor_memo[d[1]][e.pidx[plant]]))
    # --- Reallocation_PRE: direct rows and function allocations
    ps = imp.sheet.get("realloc_pre")
    if ps and "pre" in sections:
        PL = imp.layout(ps)
        cats = {norm(c): c for c in e.cfg.pre_categories}
        for r in range(5, 61):
            a, b = imp.V(ps, r, 1), imp.V(ps, r, 2)
            if not (isinstance(a, str) and isinstance(b, str)) or norm(b) not in cats:
                continue
            if norm(a) == norm(b):
                g = e.pre_direct[cats[norm(b)]]
                lab = f"PRE direct {b}"
            else:
                g = e.pre_item(cats[norm(b)], a)
                lab = f"PRE {b} - {a}"
            for c, ent in PL.entity.items():
                if ent[0] == ALL or ent[1] not in e.pgs:
                    continue
                add("pre", ps, r, lab, ent, _num(imp.V(ps, r, c)), float(g[e.pidx[ent[0]], e.kidx[ent[1]]]))
    # --- Data for reports: every translated line, every cell + plant / company totals
    if "dfr" in sections:
        ds = imp.sheet["dfr"]
        D = imp.layout(ds)
        for r, code in imp.dfr_line_of.items():
            if code not in result.lines:
                continue
            for c, ent in D.entity.items():
                if imp.F(ds, r, c) is None:
                    continue
                p = None if ent[0] == ALL else ent[0]
                g = None if ent[1] == TOTAL else ent[1]
                if not comparable(code, p):
                    continue
                if p is None and _company_block_sign_flip(imp, r):
                    continue
                exp = _num(imp.V(ds, r, c))
                if exp is None:
                    continue
                add("dfr", ds, r, imp.V(ds, r, 2), ent, exp, result.value(code, p, g))
    # --- report sheets
    if "reports" in sections:
        rs = imp.report_sets.get("pg_wise")
        if rs:
            for sh, _c0, plant in rs["instances"]:
                ws = imp.wv[sh]
                value_cols = list(imp._value_columns(sh))
                for r in range(6, ws.max_row + 1):
                    code = imp.rep_line_of.get(("pg_wise", rs["row_map"].get((sh, r))))
                    if not code or code not in result.lines or not comparable(code, plant):
                        continue
                    for c in value_cols:
                        if imp.F(sh, r, c) is None:
                            continue
                        ent = imp._value_columns(sh)[c]
                        exp = _num(imp.V(sh, r, c))
                        act = result.value(code, plant, None if ent[1] == TOTAL else ent[1])
                        add("reports", sh, r, imp.V(sh, r, 2), ent, exp, act)
        rsp = imp.report_sets.get("plant_wise")
        if rsp:
            pw = rsp["template"]
            for r in range(6, imp.wv[pw].max_row + 1):
                code = imp.rep_line_of.get(("plant_wise", r))
                if not code or code not in result.lines:
                    continue
                for c in range(3, imp.wv[pw].max_column + 1):
                    if imp.V(pw, 5, c) != "Rs" or imp.F(pw, r, c) is None:
                        continue
                    plant = imp.V(pw, 2, c)
                    p = plant if plant in e.pidx else None
                    if not comparable(code, p):
                        continue
                    add("reports", pw, r, imp.V(pw, r, 2), (p or ALL, TOTAL), _num(imp.V(pw, r, c)),
                        result.value(code, p, None))
        # PG Totals (one PG across plants) and All PG (company by PG): rows align with the PG-wise template
        if rs:
            tkeys = imp._label_keys(rs["template"])
            for name in ("PG Totals", "All PG"):
                if name not in imp.wv.sheetnames:
                    continue
                ws = imp.wv[name]
                for key, r in imp._label_keys(name).items():
                    # these sheets display the Data-for-reports lines directly (without the sign fixes
                    # and plugs that the plant P&Ls apply), so compare with the data line of that label
                    drow = imp.label_row(imp.sheet["dfr"], imp.V(name, r, 2))
                    code = imp.dfr_line_of.get(drow) if drow else None
                    if code is None:
                        trow = tkeys.get(key)
                        code = imp.rep_line_of.get(("pg_wise", trow)) if trow else None
                    if not code or code not in result.lines:
                        continue
                    for c in range(3, ws.max_column + 1):
                        if imp.V(name, 5, c) != "Rs" or imp.F(name, r, c) is None:
                            continue
                        p, g = imp.V(name, 2, c), imp.V(name, 3, c)
                        p = p if p in e.pidx else None
                        g = g if g in e.kidx else None
                        if not comparable(code, p):
                            continue
                        add("reports", name, r, imp.V(name, r, 2), (p or ALL, g or TOTAL), _num(imp.V(name, r, c)),
                            result.value(code, p, g))
    df = pd.DataFrame(rows, columns=["section", "sheet", "row", "label", "entity", "expected", "actual",
                                     "abs_diff"])
    return Comparison(df, tol, skipped)


def _company_block_sign_flip(imp: LegacyWorkbookImporter, row: int) -> bool:
    """True when the company-total block of a Data-for-reports row multiplies the plant sum by -1."""
    ds = imp.sheet["dfr"]
    D = imp.layout(ds)
    for c, ent in D.entity.items():
        if ent[0] == ALL and ent[1] != TOTAL:
            f = imp.F(ds, row, c)
            return isinstance(f, str) and f.replace(" ", "").endswith("*-1")
    return False
