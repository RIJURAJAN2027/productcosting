"""Import a legacy "Product costing – Plant wise" Excel workbook.

The importer converts the workbook's *formulas* into the engine's configuration:

* masters: business areas, GL accounts (with categories), cost centres, product groups;
* categories and their signs (from the reallocation sheet);
* allocation rules (Common / Corporate / PRE functions) including per-cell bases,
  custom cross-plant formulas and hard-coded plugs;
* the P&L line structure (from the report and "Data for reports" sheets);
* period inputs: the raw TBs (manual edits in the TB become adjustment rules),
  material-cost adjustments and MIS figures;
* report templates (row layout and styles) so exports keep the familiar format.

Everything that cannot be expressed as a dynamic rule is imported as a fixed
value and listed in ``notes`` so it can be reviewed.  The importer never writes
the workbook anywhere; callers store the result in the database.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, field
import io
import json
import re
from typing import Any

import openpyxl
import pandas as pd

from ..engine.config import EXCLUDED, NONPRE, PRE, EngineConfig, cell_key, code_str, norm
from .excel_formula import Untranslatable, col, formula_text, letter, parse_ref, references, translate_arithmetic
from . import tb as tbmod

TOTAL = "__TOTAL__"
ALL = "__ALL__"

SHEETS = {
    "ba_tb": "Input_BA TB", "cc_tb": "Input_CC TB", "masters": "Masters", "nonpre": "Non-PRE", "pre": "PRE",
    "realloc": "Reallocation_non-PRE", "alloc": "Allocation logic_non PRE", "realloc_pre": "Reallocation_PRE",
    "alloc_pre": "Allocation logic_PRE", "dfr": "Data for reports", "mc": "MC %", "plantwise": "Plant-wise PL",
    "mis": "Check with MIS Master", "instructions": "Instructions sheet", "da_pg": "Direct & allocation_PG",
    "da_plant": "Direct & allocation_Plant",
}

STD_ALLOC = re.compile(
    r"^=IFERROR\((?P<num>SUM\([^()]*\)|\$?[A-Z]{1,3}\$?\d+)/VLOOKUP\((?P<lab>[^,]+),'?(?P<alloc>[^'!]+)'?!\$B:\$[A-Z]{1,3},"
    r"MATCH\(\$?(?P<tot>[A-Z]{1,3})\$2&\$?[A-Z]{1,3}\$3&\$?[A-Z]{1,3}\$4,'?[^'!]+'?!\$1:\$1,0\)-1,\)\*"
    r"VLOOKUP\([^,]+,'?[^'!]+'?!\$B:\$[A-Z]{1,3},MATCH\(\$?(?P<x>[A-Z]{1,3})\$2&\$?[A-Z]{1,3}\$3&\$?[A-Z]{1,3}\$4,"
    r"'?[^'!]+'?!\$1:\$1,0\)-1,\),0\)(?P<plug>(?:\s*[-+]\s*\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)*)\s*$")
DFR_TERM = re.compile(
    r"^\(?\$?(?P<pc>[A-Z]{1,3})\$?(?P<pr>\d+)/'Data for reports'!\$?(?P<dc>[A-Z]{1,3})\$?(?P<dr>\d+)"
    r"\*'Data for reports'!\$?(?P<bc>[A-Z]{1,3})\$?(?P<br>\d+)\)?$")
LOOKUP_SHEET = re.compile(
    r"^=\(?\s*IFERROR\(VLOOKUP\(\$B(?P<row>\d+),'?(?P<sheet>[^'!]+)'?!\$B:\$[A-Z]{1,3},MATCH\("
    r"\$?(?P<col>[A-Z]{1,3})\$2&\$?[A-Z]{1,3}\$3&\$?[A-Z]{1,3}\$4,'?[^'!]+'?!\$1:\$1,0\)-1,\),0\)\s*\)?"
    r"(?:\s*\*\s*(?P<factor>-?\d+(?:\.\d+)?))?(?P<plug>(?:\s*[-+]\s*\d+(?:\.\d+)?)*)\s*$")
BASIS_LOOKUP = re.compile(
    r"^=(?P<iferr>IFERROR\()?_xlfn\.XLOOKUP\(\$A(?P<row>\d+),'?(?P<sheet>[^'!]+)'?!\$B:\$B,'?[^'!]+'?!"
    r"\$?(?P<col>[A-Z]{1,3}):\$?[A-Z]{1,3}\)(?(iferr),(?P<fb>-?\d*(?:\.\d+)?)\))\s*$")
SUMIF_DA = re.compile(
    r"^=SUMIF\('?(?P<sheet>[^'!]+)'?!\$A\$(?P<r1>\d+):\$A\$(?P<r2>\d+),'?[^'!]+'?!\$B\$(?P<lab>\d+),"
    r"'?[^'!]+'?!\$?(?P<c>[A-Z]{1,3})\$?(?P<s1>\d+):\$?[A-Z]{1,3}\$?(?P<s2>\d+)\)$")
SUMIF_PRE = re.compile(
    r"^=SUMIF\(\$A\$(?P<r1>\d+):\$A\$(?P<r2>\d+),\$B(?P<lab>\d+),\$?(?P<c>[A-Z]{1,3})\$?(?P<s1>\d+):"
    r"\$?[A-Z]{1,3}\$?(?P<s2>\d+)\)$")
MC_REF = re.compile(r"^='MC %'!\$?(?P<c>[A-Z]{1,3})\$?(?P<r>\d+)$")
ZERO_EDIT = re.compile(r"^=\s*\(?\s*(?P<n>-?\d+(?:\.\d+)?)\s*\)?\s*\*\s*0\s*$")
CONST_EDIT = re.compile(r"^=\s*(?P<n>-?\d+(?:\.\d+)?(?:\s*[-+]\s*\d+(?:\.\d+)?)*)\s*$")


def squash(formula: str) -> str:
    """Remove whitespace outside quoted sheet names / strings."""
    out, quote = [], None
    for ch in formula:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif not ch.isspace():
            out.append(ch)
    return "".join(out)


def slug(text: str) -> str:
    s = re.sub(r"[^0-9a-z]+", "_", str(text).casefold()).strip("_")
    return s or "line"


@dataclass
class LegacyImport:
    config: dict
    ba_tb: pd.DataFrame
    cc_tb: pd.DataFrame
    adjustments: list[dict]
    mc_adjustments: dict[str, float]
    templates: dict
    mis: list[dict]
    notes: list[dict]
    masters: dict
    trace: dict = field(default_factory=dict)

    def note_counts(self) -> dict:
        return dict(Counter(n["level"] for n in self.notes))


class _Layout:
    """Column meaning (plant x code) for a sheet with plant in row 2 and code in row 3."""

    def __init__(self, imp: "LegacyWorkbookImporter", sheet: str, plants: list[str], codes: set[str]):
        self.sheet = sheet
        self.entity: dict[int, tuple[str, str]] = {}
        self.key_col: dict[str, int] = {}
        ws = imp.wv[sheet]
        for c in range(1, ws.max_column + 1):
            k = ws.cell(1, c).value
            if k is not None and str(k) not in self.key_col:
                self.key_col[str(k)] = c
            p, g = ws.cell(2, c).value, ws.cell(3, c).value
            if p in plants and g in codes:
                self.entity[c] = (p, g)
            elif p in plants and (g == "Total" or g == p):
                self.entity[c] = (p, TOTAL)
            elif p == "Total" and g in codes:
                self.entity[c] = (ALL, g)
            elif p == "Total" and g == "Total":
                self.entity[c] = (ALL, TOTAL)
        self.cols_of_plant: dict[str, list[int]] = {}
        for c, (p, g) in self.entity.items():
            if g != TOTAL and p != ALL:
                self.cols_of_plant.setdefault(p, []).append(c)


class LegacyWorkbookImporter:
    def __init__(self, source: bytes | str):
        if isinstance(source, (bytes, bytearray)):
            self.wf = openpyxl.load_workbook(io.BytesIO(source))
            self.wv = openpyxl.load_workbook(io.BytesIO(source), data_only=True)
        else:
            self.wf = openpyxl.load_workbook(source)
            self.wv = openpyxl.load_workbook(source, data_only=True)
        self.notes: list[dict] = []
        self.sheet = self._resolve_sheets()
        self.lines: "OrderedDict[str, dict]" = OrderedDict()
        self.dfr_line_of: dict[int, str] = {}
        self.rep_line_of: dict[tuple[str, int], str] = {}
        self._layouts: dict[str, _Layout] = {}
        self.blank_cells: dict[str, set] = {}
        self._label_rows: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------ basics
    def note(self, level: str, message: str, **kw) -> None:
        self.notes.append({"level": level, "message": message, **kw})

    def _resolve_sheets(self) -> dict[str, str]:
        names = {s.casefold(): s for s in self.wf.sheetnames}
        out = {}
        for key, name in SHEETS.items():
            if name.casefold() in names:
                out[key] = names[name.casefold()]
        pgw = [s for s in self.wf.sheetnames if s.casefold().startswith("pg-wise pl")]
        if pgw:
            out["pgwise"] = pgw[0]
            out["pgwise_all"] = pgw
        missing = [k for k in ("ba_tb", "cc_tb", "masters", "nonpre", "pre", "realloc", "alloc", "dfr")
                   if k not in out]
        if missing:
            raise ValueError("This does not look like a legacy product-costing workbook; missing sheets: "
                             + ", ".join(SHEETS[m] for m in missing))
        return out

    def F(self, sheet: str, r: int, c: int):
        v = self.wf[sheet].cell(r, c).value
        t = formula_text(v)
        return t if t is not None else v

    def V(self, sheet: str, r: int, c: int):
        return self.wv[sheet].cell(r, c).value

    def layout(self, sheet: str) -> _Layout:
        if sheet not in self._layouts:
            self._layouts[sheet] = _Layout(self, sheet, self.plants, set(self.pgs) | {self.com})
        return self._layouts[sheet]

    def label_row(self, sheet: str, label: Any) -> int | None:
        """First row whose column-B value matches label (Excel lookups are case-insensitive)."""
        if sheet not in self._label_rows:
            ws = self.wv[sheet]
            m: dict[str, int] = {}
            for r in range(1, ws.max_row + 1):
                v = ws.cell(r, 2).value
                if v is not None:
                    m.setdefault(norm(v), r)
            self._label_rows[sheet] = m
        return self._label_rows[sheet].get(norm(label))

    # =============================================================== structure
    def read_structure(self) -> None:
        s = self.sheet["realloc"]
        ws = self.wv[s]
        plants, pgs, com, cor = [], [], None, None
        for c in range(3, ws.max_column + 1):
            p, g = ws.cell(2, c).value, ws.cell(3, c).value
            if g in (None,) or p in (None,):
                continue
            if g == "COR":
                cor = g
                continue
            if g == "Total":
                continue
            if p not in plants and p != "Total":
                plants.append(p)
            if g in ("COM",):
                com = g
            elif p == plants[0] and g not in pgs:
                pgs.append(g)
        self.plants, self.pgs = plants, pgs
        self.com, self.cor = com or "COM", cor or "COR"
        L = self.layout(s)
        self.realloc_cor_cols = {}
        for c in range(3, ws.max_column + 1):
            if ws.cell(3, c).value == self.cor:
                self.realloc_cor_cols[c] = ws.cell(2, c).value   # plant or 'Total'
        self.note("info", f"Reporting plants: {', '.join(plants)}; product groups: {', '.join(pgs)}.")
        _ = L

    # ================================================================= masters
    def read_masters(self) -> dict:
        ms = self.sheet["masters"]
        ws = self.wv[ms]
        bas, families = {}, {}
        cc_raw = {}
        for r in range(3, ws.max_row + 1):
            ba = ws.cell(r, 1).value
            if ba:
                plant, code, fam = ws.cell(r, 2).value, ws.cell(r, 3).value, ws.cell(r, 4).value
                bas[code_str(ba)] = {"plant": plant, "code": code, "family": fam}
                if code is not None:
                    families.setdefault(code, fam)
            cc = ws.cell(r, 10).value
            if cc not in (None, "") and str(cc).strip().lower() != "cost centre":
                cc_raw[code_str(cc)] = {"description": ws.cell(r, 11).value, "barea": ws.cell(r, 12).value,
                                        "function_name": ws.cell(r, 13).value, "function": ws.cell(r, 14).value}
        prefix_plant: dict[str, str] = {}
        for ba, info in bas.items():
            prefix_plant.setdefault(ba[:4], info["plant"])
        ccs = {}
        for cc, info in cc_raw.items():
            ccs[cc] = {**info, "plant": prefix_plant.get(str(info["barea"])[:4]) if info["barea"] else None}
        # the PRE sheet holds the mapping the workbook actually used – prefer it and report differences
        ps = self.sheet["pre"]
        pw = self.wv[ps]
        diffs = 0
        for c in range(7, pw.max_column + 1):
            cc = code_str(pw.cell(4, c).value)
            if not cc:
                continue
            plant, fn = pw.cell(2, c).value, pw.cell(3, c).value
            plant = None if plant in (0, "0", "#N/A") else plant
            fn = None if fn in (0, "0", "#N/A") else fn
            cur = ccs.setdefault(cc, {"description": None, "barea": pw.cell(1, c).value, "function_name": None})
            if cur.get("plant") != plant or cur.get("function") != fn:
                diffs += 1
            cur["plant"], cur["function"] = plant, fn
        if diffs:
            self.note("warning", f"{diffs} cost centre(s) in the PRE sheet resolve differently from the Masters "
                                 "sheet; the PRE-sheet mapping was used because the workbook used it.")
        # GL master from the Non-PRE and PRE sheets
        gls: dict[str, dict] = {}
        for key, typ in (("nonpre", NONPRE), ("pre", PRE)):
            s = self.sheet[key]
            w = self.wv[s]
            for r in range(5, w.max_row + 1):
                gl = code_str(w.cell(r, 1).value)
                if not gl or not any(ch.isdigit() for ch in gl):
                    continue
                cat = w.cell(r, 4).value
                if gl in gls:
                    self.note("error", f"GL {gl} is listed in both the Non-PRE and PRE sheets (double counted "
                                       "in the workbook); the first mapping was kept.")
                    continue
                gls[gl] = {"category": cat, "type": typ, "description": w.cell(r, 2).value,
                           "fs_description": w.cell(r, 3).value}
        self.masters = {"business_areas": bas, "families": families, "cost_centres": ccs, "gl_accounts": gls}
        return self.masters

    # ============================================================== categories
    def read_categories(self) -> None:
        s = self.sheet["realloc"]
        ws = self.wf[s]
        first_pg = min(c for c, (p, g) in self.layout(s).entity.items() if g in self.pgs)
        self.categories: list[dict] = []
        self.cat_row: dict[str, int] = {}
        self.row_desc: dict[int, tuple] = {}
        seen = set()
        for r in range(5, ws.max_row + 1):
            label = self.V(s, r, 2)
            if not isinstance(label, str):
                continue
            f = self.F(s, r, first_pg)
            if not isinstance(f, str):
                continue
            if label.endswith((" - Common", " - Corporate", " - Total")):
                continue
            if "'Non-PRE'!" in f or "Non-PRE!" in f:
                key = norm(label)
                comps = {}
                for off, comp in ((1, "common"), (2, "corporate"), (3, "total")):
                    lab2 = self.V(s, r + off, 2)
                    if isinstance(lab2, str) and lab2.lower().endswith(comp if comp != "total" else "total"):
                        comps[comp] = r + off
                if key in seen:
                    self.note("warning", f"Category {label!r} appears more than once in {s}; Excel lookups use the "
                                         "first occurrence, so the duplicate was ignored.")
                    for comp, rr in comps.items():
                        self.row_desc[rr] = ("ZERO",)
                    self.row_desc[r] = ("ZERO",)
                    continue
                seen.add(key)
                sign = -1 if squash(f).startswith("=-(") else 1
                self.categories.append({"name": label, "sign": sign})
                self.cat_row[label] = r
                self.row_desc[r] = ("C", label, "direct")
                for comp, rr in comps.items():
                    self.row_desc[rr] = ("C", label, comp)
            elif "PRE!" in f:
                pre_cat = label
                self.row_desc[r] = ("P", f"direct|{pre_cat}")
                for off, comp in ((1, "common"), (2, "corporate"), (3, "total")):
                    self.row_desc[r + off] = ("P", f"direct|{pre_cat}") if comp == "total" else ("ZERO",)
                self.note("info", f"Row {r} ({label!r}) of {s} is a memo of direct PRE '{pre_cat}' and was mapped "
                                  "to the PRE results.")

    def realloc_expr(self, row: int) -> str:
        d = self.row_desc.get(row)
        if not d or d[0] == "ZERO":
            return "0"
        if d[0] == "C":
            return f"[C:{d[1]}|{d[2]}]"
        return f"[P:{d[1]}]"

    # ================================================================ formulas
    def _entity_ref(self, base: str, at: tuple[str, str] | None, ctx: tuple[str, str] | None) -> str:
        """Add an '@PLANT|CODE' suffix when the referenced cell is not the evaluation cell."""
        if at is None or at == ctx:
            return base
        return base[:-1] + f"@{at[0]}|{at[1]}]"

    # -- Reallocation_PRE rows -> expressions
    def pre_row_expr(self, row: int, c: int, ctx: tuple[str, str] | None) -> str | None:
        s = self.sheet["realloc_pre"]
        L = self.layout(s)
        a, b = self.V(s, row, 1), self.V(s, row, 2)
        at = L.entity.get(c)
        f = self.F(s, row, c)
        if f is None:
            return None
        cats = {norm(x): x for x in self.pre_categories}
        if 5 < row <= 60 and isinstance(b, str):
            if norm(a) == norm(b) and norm(b) in cats:
                return self._entity_ref(f"[P:direct|{cats[norm(b)]}]", at, ctx)
            if isinstance(a, str) and norm(b) in cats:
                return self._entity_ref(f"[P:item|{cats[norm(b)]}|{a}]", at, ctx)
        if isinstance(f, (int, float)):
            return repr(float(f))
        m = SUMIF_PRE.match(f)
        if m:
            label = self.V(s, int(m.group("lab")), 2)
            parts = []
            for rr in range(int(m.group("r1")), int(m.group("r2")) + 1):
                if norm(self.V(s, rr, 1)) == norm(label):
                    e = self.pre_row_expr(rr, col(m.group("c")), ctx)
                    if e:
                        parts.append(e)
            return "(" + " + ".join(parts) + ")" if parts else "0"
        return self._translate(f, s, c, ctx)

    # -- generic arithmetic translation with sheet-aware references
    def _translate(self, f: str, sheet: str, c: int, ctx: tuple[str, str] | None) -> str:
        def ref(sh: str, cc: int, rr: int) -> str | None:
            if sh == self.sheet["dfr"]:
                if self.F(sh, rr, cc) is None and self.V(sh, rr, 2) is None:
                    return None
                code = self.dfr_line(rr)
                return self._entity_ref(f"[L:{code}]", self.layout(sh).entity.get(cc), ctx) if code else None
            if sh == self.sheet.get("realloc_pre"):
                return self.pre_row_expr(rr, cc, ctx)
            if sh == self.sheet["realloc"]:
                return self._entity_ref(self.realloc_expr(rr), self.layout(sh).entity.get(cc), ctx)
            if sh in getattr(self, "sheet_set", {}):
                name = self.sheet_set[sh]
                code = self.report_line(name, self.report_sets[name]["row_map"].get((sh, rr)))
                return f"[L:{code}]" if code else None
            raise Untranslatable(f"reference to {sh}!{letter(cc)}{rr}")
        return translate_arithmetic(f, sheet, ref)

    # =============================================================== DFR lines
    def _new_line(self, label: str, expr: str, **kw) -> str:
        base = slug(label)
        code, i = base, 2
        while code in self.lines:
            code, i = f"{base}_{i}", i + 1
        self.lines[code] = {"code": code, "label": str(label).strip(), "expr": expr, **kw}
        return code

    def dfr_cell_expr(self, row: int, c: int, ctx: tuple[str, str] | None) -> str | None:
        s = self.sheet["dfr"]
        f = self.F(s, row, c)
        if f is None:
            return None
        if isinstance(f, (int, float)):
            return repr(float(f))
        f2 = squash(f)
        m = LOOKUP_SHEET.match(f2)
        if m and m.group("sheet") in (self.sheet["realloc"], self.sheet.get("realloc_pre")):
            src = m.group("sheet")
            label = self.V(s, int(m.group("row")), 2)
            key = self.V(s, 1, c)
            src_row = self.label_row(src, label)
            src_col = self.layout(src).key_col.get(str(key))
            factor = float(m.group("factor")) if m.group("factor") else 1.0
            if src_row is None or src_col is None:
                return "0"
            if src == self.sheet["realloc"]:
                e = self._entity_ref(self.realloc_expr(src_row), self.layout(src).entity.get(src_col), ctx)
            else:
                e = self.pre_row_expr(src_row, src_col, ctx) or "0"
            if factor == 0:
                return "0"
            return e if factor == 1 else f"({factor:g} * {e})"
        m = SUMIF_DA.match(f2)
        if m:
            da = m.group("sheet")
            label = self.V(s, int(m.group("lab")), 2)
            parts = []
            for rr in range(int(m.group("r1")), int(m.group("r2")) + 1):
                if norm(self.V(da, rr, 1)) == norm(label):
                    lab2 = self.V(da, rr, 2)
                    rr2 = self.label_row(self.sheet["realloc"], lab2)
                    if rr2 is None:
                        self.note("warning", f"{da} row {rr}: {lab2!r} not found in the reallocation sheet.")
                        continue
                    da_c = col(m.group("c")) + (int(m.group("s1")) - int(m.group("r1"))) * 0
                    at = self.layout(da).entity.get(da_c)
                    parts.append(self._entity_ref(self.realloc_expr(rr2), at, ctx))
            return "(" + " + ".join(parts) + ")" if parts else "0"
        if MC_REF.match(f2):
            return "[MCADJ]"
        try:
            return self._translate(f, s, c, ctx)
        except Untranslatable as e:
            self.note("warning", f"{s}!{letter(c)}{row}: formula not translated ({e}); value fixed.")
            v = self.V(s, row, c)
            return repr(float(v)) if isinstance(v, (int, float)) else "0"

    def dfr_line(self, row: int) -> str | None:
        if row in self.dfr_line_of:
            return self.dfr_line_of[row]
        s = self.sheet["dfr"]
        label = self.V(s, row, 2)
        if label is None:
            return None
        code = self._new_line(label, "0", source=f"{s}!{row}", hidden=True)
        self.dfr_line_of[row] = code
        L = self.layout(s)
        per_cell: dict[tuple[str, str], str | None] = {}
        blank = set()
        for c, ent in L.entity.items():
            if ent[0] == ALL or ent[1] == TOTAL:
                continue
            e = self.dfr_cell_expr(row, c, ent)
            if e is None:
                blank.add(ent)
                # a blank Common cell is a real zero (Excel totals add it as 0); blank PG cells
                # are details the workbook does not show for that plant
                if ent[1] == self.com:
                    e = "0"
            per_cell[ent] = e
        self.blank_cells[code] = blank
        self.lines[code].update(self._consolidate(per_cell, f"{s}!{row}"))
        return code

    def _consolidate(self, per_cell: dict[tuple[str, str], str | None], where: str) -> dict:
        """Choose one expression for the line plus plant/cell overrides."""
        present = {k: v for k, v in per_cell.items() if v is not None}
        if not present:
            return {"expr": "0"}
        variants = Counter(present.values())
        from ..engine import expr as ex
        refsets = {v: {r.text for r in ex.refs(ex.parse(v))} for v in variants}
        main = None
        for v in variants:
            if all(refsets[v] >= refsets[w] for w in variants) and refsets[v]:
                main = v
                break
        if main is None:
            main = variants.most_common(1)[0][0]
        out: dict[str, Any] = {"expr": main}
        # A variant is the same line shown with fewer details when the main formula only adds
        # references to lines that are blank in that variant's cells (e.g. material cost shown
        # only as a worked-out total for some plants).  Anything else needs an override.
        cells_of: dict[str, set] = {}
        for k, v in present.items():
            cells_of.setdefault(v, set()).add(k)

        def equivalent(v: str) -> bool:
            if v == main:
                return True
            extra = refsets[main] - refsets[v]
            if not extra or not refsets[main] >= refsets[v]:
                return False
            for ref in extra:
                m = re.fullmatch(r"\[L:([^@\]]+)\]", ref)
                if not m or not cells_of[v] <= self.blank_cells.get(m.group(1), set()):
                    return False
            return True
        diff = {k: v for k, v in present.items() if not equivalent(v)}
        if diff:
            by_plant: dict[str, dict] = {}
            for (p, g), v in diff.items():
                by_plant.setdefault(p, {})[g] = v
            plant_exprs, cell_exprs = {}, {}
            for p, cells in by_plant.items():
                vals = set(cells.values())
                all_cells = [g for (pp, g) in per_cell if pp == p]
                if len(vals) == 1 and len(cells) == len(all_cells):
                    plant_exprs[p] = next(iter(vals))
                else:
                    for g, v in cells.items():
                        cell_exprs[cell_key(p, g)] = v
            if plant_exprs:
                out["plant_exprs"] = plant_exprs
            if cell_exprs:
                out["cell_exprs"] = cell_exprs
            self.note("info", f"{where}: formula differs by plant/cell; overrides recorded "
                              f"({len(plant_exprs)} plant, {len(cell_exprs)} cell).")
        return out

    # ============================================================ report lines
    def _label_keys(self, sheet: str) -> dict[tuple[str, int], int]:
        """(normalised label, occurrence) -> row; aligns report sheets whose rows are shifted."""
        keys, counts = {}, Counter()
        for r in range(6, self.wv[sheet].max_row + 1):
            lab = self.V(sheet, r, 2)
            if lab is None:
                continue
            k = norm(lab)
            counts[k] += 1
            keys[(k, counts[k])] = r
        return keys

    def _make_set(self, name: str, template: str, instances: list[tuple[str, int, str]]) -> dict:
        tkeys = self._label_keys(template)
        sheets = {i[0] for i in instances} | {template}
        row_map: dict[tuple[str, int], int] = {}
        for sh in sheets:
            for key, r in self._label_keys(sh).items():
                if key in tkeys:
                    row_map[(sh, r)] = tkeys[key]
        inv = {(t, sh): r for (sh, r), t in row_map.items()}
        pct = {sh: {c for c in range(3, self.wv[sh].max_column + 1) if self.V(sh, 5, c) == "%"} for sh in sheets}
        for sh in sheets:
            self.sheet_set[sh] = name
        return {"name": name, "template": template, "instances": instances, "row_map": row_map, "inv": inv,
                "pct_cols": pct}

    def _setup_reports(self) -> None:
        self.report_sets: dict[str, dict] = {}
        self.sheet_set: dict[str, str] = {}
        pgw = self.sheet.get("pgwise_all", [])
        if pgw:
            inst = [(sh, 3, self.V(sh, 2, 3)) for sh in pgw if self.V(sh, 2, 3) in self.plants]
            self.report_sets["pg_wise"] = self._make_set("pg_wise", self.sheet["pgwise"], inst)
        pw = self.sheet.get("plantwise")
        if pw:
            inst = [(pw, c, self.V(pw, 2, c)) for c in range(3, self.wv[pw].max_column + 1)
                    if self.V(pw, 5, c) == "Rs" and self.V(pw, 2, c) in self.plants]
            self.report_sets["plant_wise"] = self._make_set("plant_wise", pw, inst)

    def _value_columns(self, sheet: str) -> dict[int, tuple[str, str]]:
        """Report value columns -> (plant, code|TOTAL) for one report sheet."""
        out = {}
        for c in range(3, self.wv[sheet].max_column + 1):
            if self.V(sheet, 5, c) == "%":
                continue
            p, g, t = self.V(sheet, 2, c), self.V(sheet, 3, c), self.V(sheet, 4, c)
            if p not in self.plants:
                continue
            if g in self.pgs or g == self.com:
                out[c] = (p, g)
            elif t == "Total" or g == p:
                out[c] = (p, TOTAL)
        return out

    def report_line(self, set_name: str, trow: int | None) -> str | None:
        """Line for a template row of a report set, consolidated over every plant's sheet/columns.

        * PG-wise sheets: every PG / Common column is translated (cell level); when a plant-total
          column does not equal the sum of its cells the total's own formula is kept as an override.
        * Plant-wise sheet: each plant column is a plant total (``total_exprs``).
        """
        if trow is None or set_name not in self.report_sets:
            return None
        key = (set_name, trow)
        if key in self.rep_line_of:
            return self.rep_line_of[key]
        rs = self.report_sets[set_name]
        label = self.V(rs["template"], trow, 2)
        if label is None:
            self.rep_line_of[key] = None
            return None
        code = self._new_line(label, "0", source=f"{rs['template']}!{trow}")
        self.rep_line_of[key] = code
        per_cell: dict[tuple[str, str], str | None] = {}
        totals: dict[str, str] = {}
        any_formula = False
        for sh in dict.fromkeys(i[0] for i in rs["instances"]):
            r = rs["inv"].get((trow, sh))
            if r is None:
                continue
            cols = self._value_columns(sh)
            cell_vals, total_val, total_col = 0.0, None, None
            for c, ent in cols.items():
                f = self.F(sh, r, c)
                if f is None:
                    continue
                any_formula = True
                v = self.V(sh, r, c)
                if ent[1] == TOTAL:
                    total_val, total_col = v, c
                    if set_name == "plant_wise":
                        totals[ent[0]] = self._safe_report_expr(rs, sh, r, c, f, ent)
                    continue
                cell_vals += float(v) if isinstance(v, (int, float)) else 0.0
                per_cell[ent] = self._safe_report_expr(rs, sh, r, c, f, ent)
            ratio = any(isinstance(v, str) and re.search(r"/\s*[(\[]", v) for v in per_cell.values())
            if set_name != "plant_wise" and not ratio and total_col is not None \
                    and isinstance(total_val, (int, float)) and abs(total_val - cell_vals) > 1e-9:
                plant = cols[total_col][0]
                totals[plant] = self._safe_report_expr(rs, sh, r, total_col, self.F(sh, r, total_col),
                                                       (plant, TOTAL))
                self.note("warning", f"{sh} row {r} ({label!r}): the plant total ({total_val:.6f}) is not the sum "
                                     f"of its product-group cells ({cell_vals:.6f}); the workbook's total formula "
                                     "was kept as a plant-total override.")
        if not any_formula:
            del self.lines[code]
            self.rep_line_of[key] = None
            return None
        if per_cell:
            result = self._consolidate(per_cell, f"{rs['template']}!{trow}")
        else:   # plant-wise: cell formula = the most common plant-total formula
            result = {"expr": Counter(totals.values()).most_common(1)[0][0]}
        if totals:
            result["total_exprs"] = totals
        m = re.fullmatch(r"\[L:([^@\]]+)\]", result["expr"])
        referenced = any(f"[L:{code}]" in json.dumps(ln) for c2, ln in self.lines.items() if c2 != code)
        if (m and len(result) == 1 and m.group(1) in self.lines and m.group(1) != code and not referenced
                and norm(self.lines[m.group(1)]["label"]) == norm(label)):
            del self.lines[code]
            self.rep_line_of[key] = m.group(1)
            self.lines[m.group(1)]["hidden"] = False
            return m.group(1)
        self.lines[code].update(result)
        self.lines[code]["hidden"] = False
        if re.search(r"/\s*[(\[]", result["expr"]):
            self.lines[code]["additive"] = False
        return code

    def _safe_report_expr(self, rs, sh, r, c, f, ctx) -> str:
        try:
            return self._report_cell_expr(rs, sh, r, c, f, ctx)
        except Untranslatable as e:
            v = self.V(sh, r, c)
            self.note("warning", f"{sh}!{letter(c)}{r}: formula not translated ({e}); value fixed.")
            return repr(float(v)) if isinstance(v, (int, float)) else "0"

    def _report_cell_expr(self, rs: dict, sh: str, r: int, c: int, f, ctx=None) -> str:
        if isinstance(f, (int, float)):
            return repr(float(f))
        m = LOOKUP_SHEET.match(squash(f))
        if m and m.group("sheet") == self.sheet["dfr"]:
            dfr_row = self.label_row(self.sheet["dfr"], self.V(sh, int(m.group("row")), 2))
            factor = float(m.group("factor")) if m.group("factor") else 1.0
            key = "".join("" if self.V(sh, k, c) is None else str(self.V(sh, k, c)) for k in (2, 3, 4))
            dcol = self.layout(self.sheet["dfr"]).key_col.get(key)
            if dfr_row is None or factor == 0 or dcol is None:
                return "0"
            code = self.dfr_line(dfr_row)
            ref = self._entity_ref(f"[L:{code}]", self.layout(self.sheet["dfr"]).entity.get(dcol), ctx)
            out = ref if factor == 1 else f"({factor:g} * {ref})"
            plug = sum(float(x.replace(" ", "")) for x in re.findall(r"[-+]\s*\d+(?:\.\d+)?", m.group("plug") or ""))
            if plug:
                self.note("warning", f"{sh}!{letter(c)}{r}: hard-coded amount {plug:+g} added to the looked-up value; "
                                     "kept in the line formula.")
                out = f"({out} + {plug!r})"
            return out

        def ref(sh2: str, cc: int, rr: int) -> str | None:
            if sh2 == sh:
                trow = rs["row_map"].get((sh, rr))
                if cc in rs["pct_cols"][sh]:
                    code = self.report_line(rs["name"], trow)
                    base = self.report_pct_base(rs, sh, rr, cc)
                    return f"([L:{code}] / ({base}))" if code and base else None
                if cc != c:
                    raise Untranslatable(f"cross-column reference {letter(cc)}{rr}")
                code = self.report_line(rs["name"], trow)
                return f"[L:{code}]" if code else None
            if sh2 == self.sheet["dfr"]:
                code = self.dfr_line(rr)
                return f"[L:{code}]" if code else None
            other = self.sheet_set.get(sh2)
            if other:
                ors = self.report_sets[other]
                code = self.report_line(other, ors["row_map"].get((sh2, rr)))
                return f"[L:{code}]" if code else None
            raise Untranslatable(f"reference to {sh2}!{letter(cc)}{rr}")
        return translate_arithmetic(f, sh, ref)

    def report_pct_base(self, rs: dict, sh: str, row: int, pct_col: int) -> str | None:
        f = self.F(sh, row, pct_col)
        if not isinstance(f, str):
            return None
        m = re.match(r"^=IFERROR\((?P<num>[^/]+)/(?P<den>.+),0\)$", squash(f))
        if not m:
            return None

        def ref(sh2, cc, rr):
            if sh2 != sh:
                raise Untranslatable("cross-sheet % base")
            code = self.report_line(rs["name"], rs["row_map"].get((sh, rr)))
            return f"[L:{code}]" if code else None
        try:
            return translate_arithmetic("=" + m.group("den"), sh, ref)
        except Untranslatable:
            return None

    def read_report_template(self, set_name: str) -> list[dict]:
        """Template rows (label, line, % base, style) for exports in the familiar layout."""
        rs = self.report_sets[set_name]
        sheet = rs["template"]
        first = rs["instances"][0][1] if rs["instances"] else 3
        ws = self.wf[sheet]
        rows = []
        for r in range(6, ws.max_row + 1):
            label = self.V(sheet, r, 2)
            cell = ws.cell(r, 2)
            style = {"bold": bool(cell.font and cell.font.b), "italic": bool(cell.font and cell.font.i),
                     "fill": (cell.fill.fgColor.rgb if cell.fill and cell.fill.fill_type == "solid"
                              and isinstance(cell.fill.fgColor.rgb, str) else None),
                     "indent": int(cell.alignment.indent or 0) if cell.alignment else 0,
                     "number_format": ws.cell(r, first).number_format}
            if label is None:
                rows.append({"row": r, "kind": "blank"})
                continue
            code = self.report_line(set_name, r)
            pct = self.report_pct_base(rs, sheet, r, first + 1) if code else None
            if code and pct:
                self.lines[code].setdefault("pct_base", pct)
            rows.append({"row": r, "kind": "line" if code else "header", "label": str(label).strip(),
                         "line": code, "pct_base": pct, "style": style})
        return rows

    # ================================================================== rules
    def _alloc_cell_specs(self, alloc: str, arow: int) -> dict[str, dict]:
        """Basis spec for every plant x code column of one allocation-logic row."""
        L = self.layout(alloc)
        out = {}
        for c, (p, g) in L.entity.items():
            if p == ALL or g == TOTAL:
                continue
            out[cell_key(p, g)] = self.basis_spec(alloc, arow, c, (p, g))
        return out

    def basis_spec(self, sheet: str, row: int, c: int, ctx: tuple[str, str], depth: int = 0) -> dict:
        if depth > 20:
            raise Untranslatable("basis reference chain too deep")
        f = self.F(sheet, row, c)
        L = self.layout(sheet)
        if f is None:
            return {"kind": "none"}
        if isinstance(f, (int, float)):
            return {"kind": "const", "value": float(f)}
        if not isinstance(f, str):
            return {"kind": "none"}
        f2 = squash(f)
        m = BASIS_LOOKUP.match(f2)
        if m:
            label = self.V(sheet, int(m.group("row")), 1)
            src = m.group("sheet")
            src_row = self.label_row(src, label) if src in self.wv.sheetnames else None
            iferr = bool(m.group("iferr"))
            if src_row is None:
                fb = m.group("fb") if iferr else None
                return {"kind": "const", "value": float(fb) if fb else 0.0} if iferr else {"kind": "error"}
            src_col = col(m.group("col"))
            at = self.layout(src).entity.get(src_col)
            if src == self.sheet["realloc"]:
                d = self.row_desc.get(src_row, ("ZERO",))
                if d[0] == "C":
                    spec = {"kind": "component", "category": d[1], "component": d[2]}
                elif d[0] == "P":
                    spec = {"kind": "pre", "ref": d[1]}
                else:
                    return {"kind": "none"}
            elif src == self.sheet["dfr"]:
                code = self.dfr_line(src_row)
                spec = {"kind": "line", "line": code}
            else:
                v = self.V(sheet, row, c)
                return {"kind": "const", "value": float(v) if isinstance(v, (int, float)) else 0.0}
            if at and at != ctx:
                spec["at"] = cell_key(*at)
            return spec
        refs = references(f, sheet)
        if refs and all(r[0] == sheet for r in refs):
            try:
                items: list[dict] = []

                def walk(sh, cc, rr):
                    spec = self.basis_spec(sh, rr, cc, L.entity.get(cc, ctx), depth + 1)
                    ent = L.entity.get(cc)
                    if ent and ent != ctx and spec.get("kind") not in ("const", "none", "error") and "at" not in spec:
                        spec = {**spec, "at": cell_key(*ent)}
                    items.append(spec)
                    return f"[X:{len(items) - 1}]"

                expr = translate_arithmetic(f, sheet, walk)
                if re.fullmatch(r"\[X:0\]", expr):
                    return items[0]
                if re.fullmatch(r"\(?\[X:\d+\]( \+ \[X:\d+\])*\)?", expr):
                    return {"kind": "sum", "items": items}
            except Untranslatable:
                pass
        v = self.V(sheet, row, c)
        if refs and any(r[0] != sheet for r in refs):
            self.note("info", f"{sheet}!{letter(c)}{row}: basis taken as a fixed driver value from "
                              f"{refs[0][0]} ({v}).")
        else:
            self.note("warning", f"{sheet}!{letter(c)}{row}: basis formula {f!r} imported as fixed value {v}.")
        return {"kind": "const", "value": float(v) if isinstance(v, (int, float)) else 0.0}

    def _denominator(self, alloc: str, arow: int, tot_col: int) -> tuple[dict, list[str]]:
        f = self.F(alloc, arow, tot_col)
        L = self.layout(alloc)
        if isinstance(f, (int, float)):
            return {"kind": "const", "value": float(f)}, []
        if not isinstance(f, str):
            return {"kind": "const", "value": 0.0}, []
        refs = references(f, alloc)
        if refs and all(r[0] == alloc and r[2] == arow and r[4] == arow for r in refs) and \
                re.fullmatch(r"=(SUM\([^()]*\)\+?)+", squash(f)):
            cells, extra = [], 0.0
            for _, c1, _, c2, _ in refs:
                for c in range(c1, c2 + 1):
                    ent = L.entity.get(c)
                    if ent and ent[0] != ALL and ent[1] != TOTAL:
                        cells.append(cell_key(*ent))
                    else:
                        v = self.V(alloc, arow, c)
                        if isinstance(v, (int, float)) and v:
                            extra += float(v)
            plants_full = []
            for p in self.plants:
                pc = {cell_key(*L.entity[c]) for c in L.cols_of_plant.get(p, [])}
                if pc and pc <= set(cells):
                    plants_full.append(p)
            covered = {cell_key(*L.entity[c]) for p in plants_full for c in L.cols_of_plant.get(p, [])}
            spec: dict = {"kind": "basis"}
            if covered == set(cells):
                spec["plants"] = plants_full
            else:
                spec["cells"] = sorted(set(cells))
            if extra:
                spec["extra"] = extra
            return spec, cells
        if len(refs) == 1 and refs[0][0] in getattr(self, "sheet_set", {}):
            sh, c1, r1, _, _ = refs[0]
            name = self.sheet_set[sh]
            code = self.report_line(name, self.report_sets[name]["row_map"].get((sh, r1)))
            plants = "all" if self.V(sh, 2, c1) not in self.plants else [self.V(sh, 2, c1)]
            return {"kind": "line_total", "line": code, "plants": plants}, []
        v = self.V(alloc, arow, tot_col)
        self.note("warning", f"{alloc}!{letter(tot_col)}{arow}: denominator {f!r} imported as fixed value {v}.")
        return {"kind": "const", "value": float(v) if isinstance(v, (int, float)) else 0.0}, []

    def _parse_alloc_cell(self, sheet: str, row: int, c: int, alloc: str, dataset: str
                          ) -> list[dict] | None:
        """Return the allocation terms of one PG cell, [] for 'no allocation', None if unparseable."""
        f = self.F(sheet, row, c)
        L = self.layout(sheet)
        ent = L.entity.get(c)
        if f is None or isinstance(f, (int, float)):
            return [] if not f else None
        f2 = squash(f)
        m = STD_ALLOC.match(f2)
        if m:
            num = m.group("num")
            if num.upper().startswith("SUM("):
                src_cols = [c for r in references("=" + num, sheet) for c in range(r[1], r[3] + 1)]
            else:
                src_cols = [col(re.sub(r"[\$\d]", "", num))]
            sources, pool = [], None
            for sc in src_cols:
                if sheet == self.sheet["realloc"]:
                    pe = L.entity.get(sc)
                    if sc in self.realloc_cor_cols:
                        pool = self.cor
                        p = self.realloc_cor_cols[sc]
                        sources += list(self.plants) if p == "Total" else [p]
                    elif pe and pe[1] == self.com:
                        pool = self.com
                        sources.append(pe[0])
                    else:
                        return None
                else:
                    p = self.V(sheet, 2, sc)
                    if p not in self.plants:
                        return None
                    pool = "FN"
                    sources.append(p)
            if dataset == PRE:
                label = f"{self.V(sheet, row, 2)} - {self.V(sheet, row, 1)}"
            else:
                label = self.V(sheet, row, 2)
            arow = self.label_row(alloc, label)
            if arow is None:
                return []
            key_tot = str(self.V(sheet, 1, col(m.group("tot"))))
            key_x = str(self.V(sheet, 1, col(m.group("x"))))
            AL = self.layout(alloc)
            tcol, xcol = AL.key_col.get(key_tot), AL.key_col.get(key_x)
            if tcol is None or xcol is None:
                return []
            denom, _cells = self._denominator(alloc, arow, tcol)
            basis = self.basis_spec(alloc, arow, xcol, ent)
            plug = sum(float(x) for x in re.findall(r"[-+]\s*\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", m.group("plug") or ""))
            return [{"sources": sources, "pool": pool, "denominator": denom, "basis": basis, "plug": plug,
                     "alloc_row": arow}]
        if "'Data for reports'!" in f2 and sheet == self.sheet["realloc"]:
            body = f2[1:]
            terms, depth, cur = [], 0, ""
            for ch in body:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "+" and depth == 0:
                    terms.append(cur)
                    cur = ""
                else:
                    cur += ch
            terms.append(cur)
            out = []
            D = self.layout(self.sheet["dfr"])
            for t in terms:
                mt = DFR_TERM.match(t)
                if not mt:
                    return None
                pe = L.entity.get(col(mt.group("pc")))
                if not pe or pe[1] != self.com:
                    return None
                dent = D.entity.get(col(mt.group("dc")))
                code = self.dfr_line(int(mt.group("dr")))
                if not dent or dent[1] != TOTAL:
                    return None
                denom = {"kind": "line_total", "line": code,
                         "plants": "all" if dent[0] == ALL else [dent[0]]}
                bent = D.entity.get(col(mt.group("bc")))
                basis = {"kind": "line", "line": self.dfr_line(int(mt.group("br")))}
                if bent and bent != ent:
                    basis["at"] = cell_key(*bent)
                out.append({"sources": [pe[0]], "pool": self.com, "denominator": denom, "basis": basis,
                            "plug": 0.0, "alloc_row": None})
            return out
        return None

    def _build_rule(self, dataset: str, category: str, pool_code: str, sheet: str, row: int, alloc: str,
                    label: str) -> dict | None:
        L = self.layout(sheet)
        cells = [(c, ent) for c, ent in L.entity.items() if ent[0] != ALL and ent[1] in self.pgs]
        legs: "OrderedDict[str, dict]" = OrderedDict()
        basis_cells: dict[str, dict] = {}
        plugs: dict[str, float] = {}
        fixed: dict[str, float] = {}
        alloc_rows = set()
        for c, ent in cells:
            terms = self._parse_alloc_cell(sheet, row, c, alloc, dataset)
            ck = cell_key(*ent)
            if terms is None:
                v = self.V(sheet, row, c)
                fixed[ck] = float(v) if isinstance(v, (int, float)) else 0.0
                self.note("warning", f"{sheet}!{letter(c)}{row} ({label}): allocation formula not recognised; "
                                     f"imported as a fixed amount {fixed[ck]}.")
                continue
            for t in terms:
                lk = json.dumps([sorted(t["sources"], key=self.plants.index), t["denominator"]], sort_keys=True)
                leg = legs.setdefault(lk, {"sources": sorted(set(t["sources"]), key=self.plants.index),
                                           "targets": [], "denominator": t["denominator"], "basis_cells": {}})
                if ent[0] not in leg["targets"]:
                    leg["targets"].append(ent[0])
                leg["basis_cells"][ck] = t["basis"]
                if t["plug"]:
                    plugs[ck] = plugs.get(ck, 0.0) + t["plug"]
                if t.get("alloc_row"):
                    alloc_rows.add(t["alloc_row"])
        if not legs and not fixed:
            return None
        # rule-level basis cells: the whole allocation-logic row (needed for denominators)
        for arow in alloc_rows:
            for ck, spec in self._alloc_cell_specs(alloc, arow).items():
                basis_cells.setdefault(ck, spec)
        for leg in legs.values():
            for ck, spec in list(leg["basis_cells"].items()):
                if ck not in basis_cells:
                    basis_cells[ck] = spec
                if basis_cells.get(ck) == spec:
                    del leg["basis_cells"][ck]
            if not leg["basis_cells"]:
                del leg["basis_cells"]
            leg["targets"] = sorted(leg["targets"], key=self.plants.index)
        rule: dict[str, Any] = {"dataset": dataset, "category": category, "pool": pool_code, "label": label,
                                "source": f"{sheet}!{row}"}
        leg_list = list(legs.values())
        # simplify the two standard shapes
        plant_std = all(len(l["sources"]) == 1 and l["sources"] == l["targets"] and
                        l["denominator"].get("kind") == "basis" and l["denominator"].get("plants") == l["targets"]
                        and "extra" not in l["denominator"] and "basis_cells" not in l for l in leg_list)
        if plant_std and leg_list:
            rule["mode"] = "plant"
        elif (len(leg_list) == 1 and leg_list[0]["sources"] == self.plants and leg_list[0]["targets"] == self.plants
              and leg_list[0]["denominator"].get("kind") == "basis"
              and leg_list[0]["denominator"].get("plants") == self.plants
              and "extra" not in leg_list[0]["denominator"] and "basis_cells" not in leg_list[0]):
            rule["mode"] = "pooled"
        else:
            rule["mode"] = "custom"
            rule["legs"] = leg_list
        # compress basis cells: most common PG spec becomes the default
        pg_specs = [json.dumps(v, sort_keys=True) for k, v in basis_cells.items() if k.split("|")[1] in self.pgs]
        if pg_specs:
            common = Counter(pg_specs).most_common(1)[0][0]
            rule["basis"] = json.loads(common)
            rule["basis_cells"] = {k: v for k, v in basis_cells.items()
                                   if json.dumps(v, sort_keys=True) != common or k.split("|")[1] not in self.pgs}
            rule["basis_cells"] = {k: v for k, v in rule["basis_cells"].items()
                                   if not (k.split("|")[1] not in self.pgs and v.get("kind") == "none")}
        if plugs:
            rule["plugs"] = plugs
            self.note("warning", f"{label}: hard-coded plug(s) inside the allocation formulas "
                                 f"({', '.join(f'{k} {v:+g}' for k, v in plugs.items())}) kept as allocation plugs.")
        if fixed:
            rule["fixed_cells"] = fixed
        return rule

    def read_rules(self) -> list[dict]:
        s, alloc = self.sheet["realloc"], self.sheet["alloc"]
        rules = []
        for cat in self.categories:
            r = self.cat_row[cat["name"]]
            for comp, pool in (("common", self.com), ("corporate", self.cor)):
                rr = next((k for k, d in self.row_desc.items() if d == ("C", cat["name"], comp)), None)
                if rr is None:
                    continue
                rule = self._build_rule(NONPRE, cat["name"], pool, s, rr, alloc, self.V(s, rr, 2))
                if rule:
                    rules.append(rule)
            _ = r
        # PRE
        ps, palloc = self.sheet.get("realloc_pre"), self.sheet.get("alloc_pre")
        self.functions: dict[str, dict] = {}
        if ps and palloc:
            for r in range(10, 61):
                a, b = self.V(ps, r, 1), self.V(ps, r, 2)
                if not (isinstance(a, str) and isinstance(b, str)) or norm(a) == norm(b):
                    continue
                if norm(b) not in {norm(x) for x in self.pre_categories}:
                    continue
                rule = self._build_rule(PRE, b, a, ps, r, palloc, f"PRE {b} - {a}")
                if rule:
                    rule["pool"] = a
                    rules.append(rule)
                    fn = self.functions.setdefault(a, {"mode": rule["mode"], "group": None})
                    if fn["mode"] != rule["mode"]:
                        fn["mode"] = "mixed"
            # function groups from the PRE summary rows
            for r in range(61, self.wv[ps].max_row + 1):
                lab = self.V(ps, r, 2)
                f = self.F(ps, r, 3)
                if not isinstance(lab, str) or not isinstance(f, str):
                    continue
                group = "factory" if "factory" in lab.lower() else ("corporate" if "corp" in lab.lower() else None)
                if group and squash(f).upper().startswith("=SUM(") and "SUMIF" not in f.upper():
                    for _, _, r1, _, r2 in references(f, ps):
                        for rr in range(r1, r2 + 1):
                            fl = self.V(ps, rr, 2)
                            if isinstance(fl, str) and fl in self.functions:
                                self.functions[fl]["group"] = group
            for fn, info in self.functions.items():
                if info["group"] is None:
                    info["group"] = "factory"
                    self.note("warning", f"PRE function {fn!r} is not part of the factory or corporate PRE totals; "
                                         "treated as factory.")
        return rules

    # =============================================================== MC & TB
    def read_tb(self, key: str, dim: str, keep: set[str]) -> tuple[pd.DataFrame, list[dict]]:
        s = self.sheet[key]
        ws_v, ws_f = self.wv[s], self.wf[s]
        rows = [list(r) for r in ws_v.iter_rows(values_only=True)]
        parsed = tbmod.parse_rows(rows, kind=dim.upper(), keep_columns=keep)
        h = parsed.header_row
        hdr = {code_str(ws_v.cell(h, c).value): c for c in range(1, ws_v.max_column + 1)}
        adjustments: list[dict] = []
        long = parsed.long.rename(columns={"dim": dim})
        # GL rows: the workbook uses the first row per GL (VLOOKUP) – drop later duplicates
        if any(i["type"] == "duplicate_gl" for i in parsed.issues):
            self.note("warning", f"{s}: duplicate GL rows found; the workbook only uses the first one.")
        # manual edits typed into TB cells
        gl_rows = {gl: info["row"] for gl, info in parsed.gl_info.items()}
        extra_rows = []
        for gl, r in gl_rows.items():
            for d in parsed.dims:
                c = hdr.get(d)
                f = formula_text(ws_f.cell(r, c).value) if c else None
                if not f:
                    continue
                m = ZERO_EDIT.match(f)
                if m:
                    raw = float(m.group("n"))
                    if raw:
                        extra_rows.append({"gl": gl, dim: d, "amount": raw})
                        adjustments.append({"type": "amount", "tb": dim.upper(), "gl": gl, "dim": d,
                                            "amount": -raw, "reason": f"Excluded in legacy workbook input "
                                            f"(cell {s}!{letter(c)}{r} = {f})", "author": "legacy import"})
                elif not CONST_EDIT.match(f):
                    self.note("info", f"{s}!{letter(c)}{r}: formula {f!r} in TB input – cached value used.")
        if extra_rows:
            long = pd.concat([long, pd.DataFrame(extra_rows)], ignore_index=True)
            self.note("warning", f"{s}: {len(extra_rows)} TB cell(s) were zeroed by formula (=amount*0). The "
                                 "original amounts were restored and matching exclusion adjustments created.")
        ignored = [c for c in parsed.ignored_columns if c]
        if ignored:
            self.note("info", f"{s}: {len(ignored)} column(s) not in the masters were ignored as the workbook "
                              f"does: {', '.join(ignored[:10])}{'…' if len(ignored) > 10 else ''}.")
        return long[["gl", "gl_desc", dim, "amount"]].fillna({"gl_desc": ""}), adjustments

    def read_sheet_edits(self) -> list[dict]:
        """Cells of the Non-PRE / PRE sheets that do not simply look up the TB are manual edits;
        each becomes an explicit adjustment (sheet value - TB value)."""
        std = re.compile(r"^=IFERROR\(VLOOKUP\(\$A\d+,'?Input_(BA|CC) TB'?!\$A:\$[A-Z]+,MATCH\([A-Z]+\$4,"
                         r"'?Input_(BA|CC) TB'?!\$3:\$3,0\),0\),0\)$")
        out: list[dict] = []
        for key, tbkey, tb in (("nonpre", "ba_tb", "BA"), ("pre", "cc_tb", "CC")):
            s, ts = self.sheet[key], self.sheet[tbkey]
            wv, wf, tv = self.wv[s], self.wf[s], self.wv[ts]
            # TB values by (gl, dim) - first row per GL, as VLOOKUP does
            rows = [list(r) for r in tv.iter_rows(values_only=True)]
            hrow, gcol = tbmod.find_header_row(rows)
            hdr = {code_str(v): i for i, v in enumerate(rows[hrow]) if v is not None}
            first_row = {}
            for i in range(hrow + 1, len(rows)):
                gl = code_str(rows[i][gcol] if gcol < len(rows[i]) else None)
                if gl and gl not in first_row:
                    first_row[gl] = i
            for c in range(7, wv.max_column + 1):
                dim = code_str(wv.cell(4, c).value)
                if not dim or dim not in hdr:
                    continue
                for r in range(5, wv.max_row + 1):
                    gl = code_str(wv.cell(r, 1).value)
                    if not gl or not any(ch.isdigit() for ch in gl):
                        continue
                    f = formula_text(wf.cell(r, c).value)
                    raw = wf.cell(r, c).value
                    if f is not None and std.match(squash(f)):
                        continue
                    if f is None and raw is None:
                        continue
                    val = wv.cell(r, c).value
                    val = float(val) if isinstance(val, (int, float)) else 0.0
                    ti = first_row.get(gl)
                    base = rows[ti][hdr[dim]] if ti is not None and hdr[dim] < len(rows[ti]) else 0.0
                    base = float(base) if isinstance(base, (int, float)) else 0.0
                    if abs(val - base) > 1e-9:
                        out.append({"type": "amount", "tb": tb, "gl": gl, "dim": dim, "amount": val - base,
                                    "reason": f"Manual edit in legacy sheet {s}!{letter(c)}{r}: {f or raw!r}",
                                    "author": "legacy import"})
        if out:
            self.note("warning", f"{len(out)} manual edit(s) found inside the Non-PRE/PRE sheets; imported as "
                                 "adjustment rules: " + "; ".join(f"GL {a['gl']} @ {a['dim']} {a['amount']:+,.2f}"
                                                                   for a in out[:5]))
        return out

    def read_mc_adjustments(self, mc_line: str, cached_eval) -> dict[str, float]:
        """MC adjustment = worked-out material cost (workbook) - material cost from the TB components."""
        s = self.sheet["dfr"]
        row = next((r for r, code in self.dfr_line_of.items() if code == mc_line), None)
        out: dict[str, float] = {}
        if row is None:
            return out
        L = self.layout(s)
        for c, ent in L.entity.items():
            if ent[0] == ALL or ent[1] == TOTAL:
                continue
            v = self.V(s, row, c)
            target = float(v) if isinstance(v, (int, float)) else 0.0
            base = cached_eval(mc_line, ent, mcadj=False)
            adj = target - base
            if abs(adj) > 1e-12:
                out[cell_key(*ent)] = adj
        if out:
            self.note("info", f"Material-cost adjustments imported for {len(out)} cell(s) "
                              f"(net {sum(out.values()):+.6f} in calculation units).")
        return out

    # ================================================================ cached evaluation
    def cached_component(self, cat: str, comp: str, ent: tuple[str, str]) -> float:
        s = self.sheet["realloc"]
        row = next((r for r, d in self.row_desc.items() if d == ("C", cat, comp)), None)
        if row is None:
            return 0.0
        L = self.layout(s)
        c = next((cc for cc, e in L.entity.items() if e == ent), None)
        v = self.V(s, row, c) if c else None
        return float(v) if isinstance(v, (int, float)) else 0.0

    def make_cached_eval(self):
        from ..engine import expr as ex
        cats = {norm(c["name"]): c["name"] for c in self.categories}

        def evaluate(code: str, ent: tuple[str, str], mcadj: bool = True) -> float:
            ln = self.lines[code]
            expr = ln.get("cell_exprs", {}).get(cell_key(*ent)) or ln.get("plant_exprs", {}).get(ent[0]) \
                or ln.get("expr") or "0"

            def resolve(ref):
                body, _, at = ref.body.partition("@")
                e2 = tuple(at.split("|")) if at else ent
                if ref.kind == "L":
                    return evaluate(body, e2, mcadj)
                if ref.kind == "C":
                    cat, _, comp = body.partition("|")
                    cat = cats.get(norm(cat))
                    return self.cached_component(cat, comp or "total", e2) if cat else 0.0
                if ref.kind == "MCADJ":
                    return self.mc_adj.get(cell_key(*ent), 0.0) if mcadj else 0.0
                return 0.0
            return float(ex.evaluate(expr, resolve))
        return evaluate

    # ================================================================== MIS
    def read_mis(self) -> list[dict]:
        s = self.sheet.get("mis")
        pw = self.sheet.get("plantwise")
        if not s or not pw:
            return []
        out = []
        ws = self.wf[s]
        for r in range(1, ws.max_row + 1):
            label, mis, prd, diff = (self.V(s, r, 3), self.V(s, r, 4), self.F(s, r, 5), self.F(s, r, 6))
            if not isinstance(label, str) or not isinstance(prd, str) or not isinstance(mis, (int, float)):
                continue
            try:
                expr = translate_arithmetic(prd, s, lambda sh, cc, rr: (
                    f"[L:{self.report_line('plant_wise', rr)}]"
                    if sh == pw and self.report_line("plant_wise", rr) else None))
            except Untranslatable:
                continue
            mode = "difference" if isinstance(diff, str) and re.match(r"^=D\d+-E\d+$", squash(diff)) \
                else "sum"
            out.append({"label": label.strip(), "mis_value": float(mis), "expr": expr, "compare": mode,
                        "source": f"{s}!{r}"})
        return out

    # ================================================================== main
    def run(self) -> LegacyImport:
        self.read_structure()
        masters = self.read_masters()
        self.pre_categories = sorted({v["category"] for v in masters["gl_accounts"].values()
                                      if v["type"] == PRE and v["category"]},
                                     key=lambda x: ["Wages", "Salaries", "Others"].index(x)
                                     if x in ("Wages", "Salaries", "Others") else 99)
        self.read_categories()
        self._setup_reports()
        templates = {name: self.read_report_template(name) for name in self.report_sets}
        rules = self.read_rules()
        # every labelled data row becomes a (hidden) line, so any report can be re-created
        dws = self.wv[self.sheet["dfr"]]
        for r in range(6, dws.max_row + 1):
            if isinstance(dws.cell(r, 2).value, str):
                self.dfr_line(r)
        # lines referenced only by bases may not exist yet – rules already created them via dfr_line
        mc_row = self.label_row(self.sheet["dfr"], "Material cost - Total")
        mc_line = self.dfr_line(mc_row) if mc_row else None
        sales_row = self.label_row(self.sheet["dfr"], "Sales - Total")
        sales_line = self.dfr_line(sales_row) if sales_row else None
        self.mc_adj: dict[str, float] = {}
        cached_eval = self.make_cached_eval()
        if mc_line:
            self.mc_adj = self.read_mc_adjustments(mc_line, cached_eval)
        for ln in self.lines.values():
            if ln.get("expr") == "[MCADJ]":
                ln["label"] = ln["label"] + " (adjustment vs TB)"
        # TBs
        nonpre_bas = {code_str(self.V(self.sheet["nonpre"], 4, c))
                      for c in range(7, self.wv[self.sheet["nonpre"]].max_column + 1)
                      if self.V(self.sheet["nonpre"], 4, c)}
        pre_ccs = {code_str(self.V(self.sheet["pre"], 4, c))
                   for c in range(7, self.wv[self.sheet["pre"]].max_column + 1) if self.V(self.sheet["pre"], 4, c)}
        ba_tb, adj1 = self.read_tb("ba_tb", "ba", nonpre_bas)
        cc_tb, adj2 = self.read_tb("cc_tb", "cc", pre_ccs)
        adj2 = adj2 + self.read_sheet_edits()
        # GLs in the TB that the workbook never used become EXCLUDED in the GL master
        gls = masters["gl_accounts"]
        unused = sorted(set(ba_tb["gl"]) - set(gls))
        for gl in unused:
            desc = ba_tb.loc[ba_tb["gl"] == gl, "gl_desc"].iloc[0]
            gls[gl] = {"category": None, "type": EXCLUDED, "description": desc,
                       "note": "Not used by the legacy workbook"}
        if unused:
            amt = ba_tb[ba_tb["gl"].isin(unused)]["amount"].sum()
            self.note("info", f"{len(unused)} GL(s) in the BA TB are not used by the workbook and were added to the "
                              f"GL master as EXCLUDED (net amount {amt:,.2f}).")
        # categories not used anywhere are dropped (e.g. placeholder rows)
        used = {norm(v["category"]) for v in gls.values() if v.get("category")}
        blob = json.dumps(rules) + json.dumps(list(self.lines.values()))
        cats = []
        for c in self.categories:
            if norm(c["name"]) in used or f"C:{c['name']}|" in blob or f'"category": "{c["name"]}"' in blob:
                cats.append(c)
        dropped = [c["name"] for c in self.categories if c not in cats]
        if dropped:
            self.note("info", f"Unused placeholder categories dropped: {', '.join(dropped)}.")
        rules = [r for r in rules if r["dataset"] == PRE or any(norm(r["category"]) == norm(c["name"]) for c in cats)]
        missing = sorted({v["category"] for v in gls.values() if v["type"] == NONPRE and v.get("category")
                          and norm(v["category"]) not in {norm(c["name"]) for c in cats}})
        if missing:
            self.note("error", "GL categories with no row in the reallocation sheet (their amounts never reach the "
                               f"P&L in the workbook): {', '.join(missing)}.")
        pbt_row = None
        if templates.get("pg_wise"):
            pbt_row = next((t for t in templates["pg_wise"] if t.get("label", "").strip().upper() == "PBT"), None)
        functions = dict(self.functions)
        for p in self.pgs:
            functions.pop(p, None)
        cfg = EngineConfig(
            plants=self.plants, product_groups=self.pgs, com_code=self.com, cor_code=self.cor,
            calc_unit_divisor=float(self.V(self.sheet["realloc"], 1, 2) or 1e7),
            business_areas={k: {"plant": v["plant"], "code": v["code"]} for k, v in masters["business_areas"].items()},
            gl_accounts={k: {"category": v["category"], "type": v["type"], "description": v.get("description")}
                         for k, v in gls.items()},
            cost_centres={k: {"plant": v.get("plant"), "function": v.get("function"), "barea": v.get("barea")}
                          for k, v in masters["cost_centres"].items()},
            categories=cats, pre_categories=self.pre_categories, functions=functions, rules=rules,
            lines=list(self.lines.values()),
            meta={"pbt_line": pbt_row["line"] if pbt_row else None, "sales_line": sales_line,
                  "mc_line": mc_line, "source": "legacy workbook"},
        )
        for p in cfg.validate():
            self.note(p["level"], "Config: " + p["message"])
        mis = self.read_mis()
        return LegacyImport(
            config=cfg.to_dict(), ba_tb=ba_tb, cc_tb=cc_tb, adjustments=adj1 + adj2, mc_adjustments=self.mc_adj,
            templates=templates, mis=mis, notes=self.notes,
            masters={"families": masters["families"], "business_areas": masters["business_areas"],
                     "cost_centres": masters["cost_centres"], "gl_accounts": gls},
            trace={"dfr_line_of": self.dfr_line_of, "rep_line_of": {f"{k[0]}!{k[1]}": v
                                                                      for k, v in self.rep_line_of.items()},
                   "sheets": self.sheet, "row_desc": {str(k): list(v) for k, v in self.row_desc.items()}},
        )


def import_legacy_workbook(source: bytes | str) -> LegacyImport:
    return LegacyWorkbookImporter(source).run()
