"""The product-costing calculation engine.

Pipeline (each step is a separate, unit-tested method):

1. ``apply_adjustments`` – configurable adjustment rules on the long-format TBs
   (regroup GL, move to another BA / cost centre, fixed corrections, exclude).
2. ``tag`` – join the BA TB to the GL and business-area masters (Non-PRE) and
   the CC TB to the GL and cost-centre masters (PRE).  Every row gets a status
   so nothing can silently disappear.
3. ``aggregate`` – sum by category x plant x code (PG / COM / COR) in the
   calculation unit (default crore = /1,00,00,000) with category signs.
4. ``allocate`` – spread Common and Corporate pools (Non-PRE) and service
   function pools (PRE) to product groups using the allocation rules.  Results
   are computed lazily so bases may reference other results (e.g. allocate
   overheads on post-allocation sales); dependency cycles are reported.
5. ``build P&L`` – evaluate the P&L line structure for every plant x PG cell.
6. ``controls`` – TB tie-out, allocation in = allocation out, unmapped amounts,
   category coverage of PBT, MC adjustment impact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Any, Iterable

import numpy as np
import pandas as pd

from . import expr as ex
from .config import (COMPONENTS, EXCLUDED, NONPRE, PRE, EngineConfig, cell_key, code_str, norm,
                     split_cell_key)

TOL = 1e-6  # tolerance in calculation units for control checks (1e-6 crore = Rs 10)


class EngineError(RuntimeError):
    pass


@dataclass
class EngineInputs:
    """Period data for one calculation run."""

    ba_tb: pd.DataFrame                      # columns: gl, ba, amount (+ gl_desc optional)
    cc_tb: pd.DataFrame                      # columns: gl, cc, amount
    adjustments: list[dict] = field(default_factory=list)
    drivers: dict[str, dict[str, float]] = field(default_factory=dict)   # name -> {"PLANT|CODE": value}
    mc_adjustments: dict[str, Any] = field(default_factory=dict)         # "PLANT|CODE" -> amount | {kind, value}


@dataclass
class Control:
    code: str
    level: str          # ok | warning | error
    title: str
    amount: float = 0.0  # in calculation units
    details: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "title": self.title,
                "amount": self.amount, "details": self.details}


def _empty_tb(dim: str) -> pd.DataFrame:
    return pd.DataFrame({"gl": pd.Series(dtype=str), dim: pd.Series(dtype=str),
                         "amount": pd.Series(dtype=float)})


def normalise_tb(df: pd.DataFrame | None, dim: str) -> pd.DataFrame:
    """Return a clean long TB: gl/dim as strings, numeric amounts, zero rows dropped."""
    if df is None or len(df) == 0:
        return _empty_tb(dim)
    out = df.copy()
    out["gl"] = out["gl"].map(code_str)
    out[dim] = out[dim].map(code_str)
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0.0).astype(float)
    out = out[(out["gl"] != "") & (out[dim] != "") & (out["amount"] != 0.0)]
    keep = ["gl", dim, "amount"] + [c for c in ("gl_desc",) if c in out.columns]
    return out[keep].reset_index(drop=True)


class CostingEngine:
    def __init__(self, config: EngineConfig, inputs: EngineInputs):
        self.cfg = config
        self.inp = inputs
        self.plants = list(config.plants)
        self.pgs = list(config.product_groups)
        self.codes = config.codes
        self.P, self.K = len(self.plants), len(self.codes)
        self.pidx = {p: i for i, p in enumerate(self.plants)}
        self.kidx = {c: i for i, c in enumerate(self.codes)}
        self.com = self.kidx[config.com_code]
        self.div = float(config.calc_unit_divisor)
        self.cat_lookup = config.category_lookup()
        self.cat_sign = {c["name"]: int(c.get("sign", 1)) for c in config.categories}
        self.pre_cat_lookup = {norm(c): c for c in config.pre_categories}
        self.lines = config.line_lookup()
        self.line_by_label = config.line_by_label()
        self.functions = {k: dict(v) for k, v in (config.functions or {}).items()}
        self.rules: dict[tuple[str, str, str], dict] = {}
        for r in config.rules:
            ds = r.get("dataset", NONPRE)
            cat = self.cat_lookup.get(norm(r.get("category"))) if ds == NONPRE else \
                self.pre_cat_lookup.get(norm(r.get("category")), r.get("category"))
            if cat is None:
                continue
            self.rules[(ds, cat, r.get("pool"))] = r
        # lazily-computed caches
        self._comp: dict[tuple[str, str], np.ndarray] = {}
        self._line: dict[str, np.ndarray] = {}
        self._pre_item: dict[tuple[str, str], np.ndarray] = {}
        self._stack: list[str] = []
        self.alloc_log: list[dict] = []
        self.adjust_log: list[dict] = []
        self.warnings: list[str] = []
        self.timings: dict[str, float] = {}

    # ================================================================== helpers
    def zeros(self) -> np.ndarray:
        return np.zeros((self.P, self.K))

    def _parse_cell(self, key: str) -> tuple[int, int] | None:
        plant, code = split_cell_key(key)
        if plant in self.pidx and code in self.kidx:
            return self.pidx[plant], self.kidx[code]
        return None

    def _plants_arg(self, value: Any, default: Iterable[str]) -> list[str]:
        if value in (None, "", "targets"):
            return list(default)
        if value == "all":
            return list(self.plants)
        if isinstance(value, str):
            return [value]
        return [p for p in value if p in self.pidx]

    # ============================================================ 1. adjustments
    def apply_adjustments(self, ba: pd.DataFrame, cc: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        frames = {"BA": ba.copy(), "CC": cc.copy()}
        dims = {"BA": "ba", "CC": "cc"}
        for i, rule in enumerate(self.inp.adjustments or []):
            if rule.get("enabled", True) is False:
                continue
            kind = rule.get("type")
            tbs = ["BA", "CC"] if rule.get("tb", "BA") in ("BOTH", "ALL") else [rule.get("tb", "BA")]
            moved_total = 0.0
            for tb in tbs:
                df, dim = frames[tb], dims[tb]
                if kind == "regroup_gl":
                    src = {code_str(g) for g in rule.get("from_gls", [])}
                    mask = df["gl"].isin(src)
                    moved_total += df.loc[mask, "amount"].sum()
                    df.loc[mask, "gl"] = code_str(rule["to_gl"])
                elif kind == "move_dim":
                    mask = df["gl"] == code_str(rule["gl"])
                    if rule.get("from"):
                        mask &= df[dim].isin({code_str(x) for x in rule["from"]})
                    moved_total += df.loc[mask, "amount"].sum()
                    df.loc[mask, dim] = code_str(rule["to"])
                elif kind == "exclude_gl":
                    mask = df["gl"].isin({code_str(g) for g in rule.get("gls", [])})
                    if rule.get("dims"):
                        mask &= df[dim].isin({code_str(x) for x in rule["dims"]})
                    moved_total += df.loc[mask, "amount"].sum()
                    df = df[~mask]
                elif kind == "amount":
                    row = {"gl": code_str(rule["gl"]), dim: code_str(rule["dim"]), "amount": float(rule["amount"])}
                    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
                    moved_total += float(rule["amount"])
                elif kind == "reclass":
                    amt = float(rule["amount"])
                    src, dst = rule["from"], rule["to"]
                    rows = [
                        {"gl": code_str(src["gl"]), dim: code_str(src["dim"]), "amount": -amt},
                        {"gl": code_str(dst.get("gl", src["gl"])), dim: code_str(dst["dim"]), "amount": amt},
                    ]
                    df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
                    moved_total += amt
                else:
                    raise EngineError(f"Unknown adjustment type {kind!r} (rule {i + 1}).")
                frames[tb] = df
            self.adjust_log.append({"index": i, "type": kind, "reason": rule.get("reason"),
                                    "author": rule.get("author"), "amount": float(moved_total),
                                    "rule": rule})
        return frames["BA"], frames["CC"]

    # ================================================================== 2. tag
    def tag(self, ba: pd.DataFrame, cc: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        gl = self.cfg.gl_accounts
        gl_type = {code_str(k): v.get("type") for k, v in gl.items()}
        gl_cat = {code_str(k): v.get("category") for k, v in gl.items()}
        ba_m = {code_str(k): v for k, v in self.cfg.business_areas.items()}
        cc_m = {code_str(k): v for k, v in self.cfg.cost_centres.items()}

        # ---- Non-PRE from the business-area TB
        np_ = ba.copy()
        np_["gl_type"] = np_["gl"].map(gl_type)
        np_["category_raw"] = np_["gl"].map(gl_cat)
        np_["category"] = np_["category_raw"].map(lambda c: self.cat_lookup.get(norm(c)) if isinstance(c, str) else None)
        np_["plant"] = np_["ba"].map(lambda b: (ba_m.get(b) or {}).get("plant"))
        np_["code"] = np_["ba"].map(lambda b: (ba_m.get(b) or {}).get("code"))
        status = np.full(len(np_), "ok", dtype=object)
        gtype = np_["gl_type"].to_numpy(dtype=object)
        status[pd.isna(np_["gl_type"]).to_numpy()] = "unmapped_gl"
        status[gtype == PRE] = "pre_gl"            # PRE comes from the cost-centre TB
        status[gtype == EXCLUDED] = "excluded_gl"
        is_np = gtype == NONPRE
        status[is_np & pd.isna(np_["category"]).to_numpy()] = "category_not_in_pnl"
        ba_missing = is_np & ~np_["ba"].isin(ba_m.keys()).to_numpy()
        status[ba_missing] = "unmapped_ba"
        okish = is_np & (status == "ok")
        outside = okish & ~np_["plant"].isin(self.pidx.keys()).to_numpy()
        status[outside] = "outside_plants"
        known_codes = set(self.codes) | {self.cfg.cor_code}
        bad_code = okish & ~outside & ~np_["code"].isin(known_codes).to_numpy()
        status[bad_code] = "unreported_code"
        np_["status"] = status

        # ---- PRE from the cost-centre TB
        pr = cc.copy()
        pr["gl_type"] = pr["gl"].map(gl_type)
        pr["category_raw"] = pr["gl"].map(gl_cat)
        pr["category"] = pr["category_raw"].map(lambda c: self.pre_cat_lookup.get(norm(c)) if isinstance(c, str) else None)
        pr["plant"] = pr["cc"].map(lambda c: (cc_m.get(c) or {}).get("plant"))
        pr["function"] = pr["cc"].map(lambda c: (cc_m.get(c) or {}).get("function"))
        st = np.full(len(pr), "ok", dtype=object)
        ptype = pr["gl_type"].to_numpy(dtype=object)
        st[ptype != PRE] = "not_pre"               # non-PRE GLs come from the BA TB
        is_pre = ptype == PRE
        st[is_pre & pd.isna(pr["category"]).to_numpy()] = "category_not_in_pnl"
        cc_missing = is_pre & ~pr["cc"].isin(cc_m.keys()).to_numpy()
        st[cc_missing] = "unmapped_cc"
        ok2 = is_pre & (st == "ok")
        out2 = ok2 & ~pr["plant"].isin(self.pidx.keys()).to_numpy()
        st[out2] = "outside_plants"
        valid_fn = set(self.pgs) | set(self.functions.keys())
        bad_fn = ok2 & ~out2 & ~pr["function"].isin(valid_fn).to_numpy()
        st[bad_fn] = "unknown_function"
        pr["status"] = st
        return np_, pr

    # ============================================================ 3. aggregate
    def aggregate(self, np_facts: pd.DataFrame, pre_facts: pd.DataFrame) -> None:
        self.direct: dict[str, np.ndarray] = {c["name"]: self.zeros() for c in self.cfg.categories}
        self.cor_memo: dict[str, np.ndarray] = {c["name"]: np.zeros(self.P) for c in self.cfg.categories}
        ok = np_facts[np_facts["status"] == "ok"]
        if len(ok):
            g = ok.groupby(["category", "plant", "code"], sort=False)["amount"].sum()
            for (cat, plant, code), amt in g.items():
                val = self.cat_sign[cat] * amt / self.div
                if code == self.cfg.cor_code:
                    self.cor_memo[cat][self.pidx[plant]] += val
                else:
                    self.direct[cat][self.pidx[plant], self.kidx[code]] += val
        # PRE: direct to PG when the function is a PG code, else pooled by function
        self.pre_direct: dict[str, np.ndarray] = {c: self.zeros() for c in self.cfg.pre_categories}
        self.pre_pool: dict[tuple[str, str], np.ndarray] = {}
        okp = pre_facts[pre_facts["status"] == "ok"]
        if len(okp):
            g = okp.groupby(["category", "plant", "function"], sort=False)["amount"].sum()
            for (cat, plant, fn), amt in g.items():
                val = amt / self.div
                if fn in self.kidx and fn != self.cfg.com_code and fn in self.pgs:
                    self.pre_direct[cat][self.pidx[plant], self.kidx[fn]] += val
                else:
                    self.pre_pool.setdefault((cat, fn), np.zeros(self.P))[self.pidx[plant]] += val

    # =========================================================== basis & refs
    def _pick(self, grid: np.ndarray, at: str) -> float:
        """Value of a grid at 'PLANT|CODE' (CODE may be __TOTAL__, PLANT may be __ALL__)."""
        plant, code = split_cell_key(at)
        rows = slice(None) if plant == "__ALL__" else self.pidx.get(plant)
        cols = slice(None) if code == "__TOTAL__" else self.kidx.get(code)
        if rows is None or cols is None:
            return 0.0
        return float(np.sum(grid[rows, cols]))

    def _spec_grid(self, spec: dict | None) -> np.ndarray:
        """Evaluate a basis spec to a full grid (NaN marks an error)."""
        if not spec:
            return self.zeros()
        kind = spec.get("kind")
        if kind == "const":
            g = np.full((self.P, self.K), float(spec.get("value", 0.0)))
        elif kind == "none":
            g = self.zeros()
        elif kind == "error":
            g = np.full((self.P, self.K), np.nan)
        elif kind == "component":
            cat = self.cat_lookup.get(norm(spec.get("category")))
            if cat is None:
                g = np.full((self.P, self.K), float(spec.get("fallback", 0.0)))
            else:
                g = self.component(cat, spec.get("component", "direct"))
        elif kind == "line":
            g = self.line(spec["line"])
        elif kind == "pre":
            g = self._pre_ref(spec.get("ref", "direct"))
        elif kind == "driver":
            table = self.inp.drivers.get(spec.get("driver"), {})
            g = self.zeros()
            for key, val in table.items():
                ij = self._parse_cell(key)
                if ij:
                    g[ij] = float(val)
        elif kind == "sum":
            g = self.zeros()
            for item in spec.get("items", []):
                g = g + self._spec_grid(item)
        else:
            raise EngineError(f"Unknown basis kind {kind!r}")
        if spec.get("at"):
            g = np.full((self.P, self.K), self._pick(g, spec["at"]))
        if "scale" in spec:
            g = g * float(spec["scale"])
        return g

    def _basis(self, rule: dict, leg: dict | None = None) -> np.ndarray:
        grid = self.zeros()
        npg = len(self.pgs)
        default = (leg or {}).get("basis") or rule.get("basis")
        if default:
            g = self._spec_grid(default)
            grid[:, :npg] = g[:, :npg]  # defaults apply to PG cells only
        for cells in (rule.get("basis_cells"), (leg or {}).get("basis_cells")):
            for key, spec in (cells or {}).items():
                ij = self._parse_cell(key)
                if ij is None:
                    continue
                grid[ij] = self._spec_grid(spec)[ij]
        return grid

    # ============================================================ 4. allocate
    def _legs(self, rule: dict) -> list[dict]:
        if rule.get("legs"):
            return rule["legs"]
        mode = rule.get("mode") or ("pooled" if rule.get("pool") == self.cfg.cor_code else "plant")
        if mode == "plant":
            return [{"sources": [p], "targets": [p], "denominator": rule.get("denominator")} for p in self.plants]
        return [{"sources": list(self.plants), "targets": list(self.plants),
                 "denominator": rule.get("denominator")}]

    def _denominator(self, rule: dict, leg: dict, basis: np.ndarray, targets: list[str]) -> float:
        """Denominator of a leg.  Default: sum of the basis over every cell (PGs and COM) of the
        target plants.  Alternatives: explicit ``plants`` or ``cells``, ``line_total`` (sum of a
        P&L line over plants) or a ``const``; ``extra`` adds a fixed amount."""
        spec = leg.get("denominator") or {}
        kind = spec.get("kind", "basis")
        plants = self._plants_arg(spec.get("plants", spec.get("scope")), targets)
        rows = [self.pidx[p] for p in plants]
        if kind == "const":
            value = float(spec.get("value", 0.0))
        elif kind == "line_total":
            value = float(self.line(spec["line"])[rows, :].sum())
        elif spec.get("cells"):
            value = 0.0
            for key in spec["cells"]:
                ij = self._parse_cell(key)
                if ij:
                    value += float(basis[ij])
        else:
            value = float(basis[rows, :].sum())
        extra = spec.get("extra", leg.get("denominator_extra", rule.get("denominator_extra", 0.0)))
        if isinstance(extra, dict):
            value += float(sum(v for p, v in extra.items() if p in plants))
        else:
            value += float(extra or 0.0)
        return value

    def _run_rule(self, rule: dict, pools: np.ndarray, label: str) -> np.ndarray:
        out = self.zeros()
        npg = len(self.pgs)
        for li, leg in enumerate(self._legs(rule)):
            sources = self._plants_arg(leg.get("sources"), self.plants)
            targets = self._plants_arg(leg.get("targets"), self.plants)
            pool = float(sum(pools[self.pidx[p]] for p in sources))
            basis = self._basis(rule, leg)
            denom = self._denominator(rule, leg, basis, targets)
            rows = [self.pidx[p] for p in targets]
            entry = {"rule": label, "leg": li, "sources": sources, "targets": targets,
                     "pool": pool, "denominator": denom, "allocated": 0.0, "status": "ok"}
            if pool == 0.0:
                entry["status"] = "empty"
            elif not math.isfinite(denom) or denom == 0.0 or np.isnan(basis[rows, :npg]).any():
                entry["status"] = "zero_or_invalid_basis"
            else:
                share = pool * basis[rows, :npg] / denom
                share = np.where(np.isfinite(share), share, 0.0)
                out[rows, :npg] += share
                entry["allocated"] = float(share.sum())
            self.alloc_log.append(entry)
        for key, amount in (rule.get("plugs") or {}).items():
            ij = self._parse_cell(key)
            if ij:
                out[ij] += float(amount)
                self.alloc_log.append({"rule": label, "leg": "plug", "cell": key, "pool": 0.0,
                                       "denominator": None, "allocated": float(amount), "status": "plug"})
        return out

    def _alloc_nonpre(self, cat: str, pool_code: str) -> np.ndarray:
        rule = self.rules.get((NONPRE, cat, pool_code))
        if pool_code == self.cfg.com_code:
            pools = self.direct[cat][:, self.com]
        else:
            pools = self.cor_memo[cat]
        label = f"{cat} - {'Common' if pool_code == self.cfg.com_code else 'Corporate'}"
        if rule is None:
            if np.abs(pools).sum() > 0:
                self.alloc_log.append({"rule": label, "leg": None, "sources": self.plants, "targets": [],
                                       "pool": float(pools.sum()), "denominator": None, "allocated": 0.0,
                                       "status": "no_rule"})
            return self.zeros()
        out = self._run_rule(rule, pools, label)
        if pool_code == self.cfg.com_code:
            out[:, self.com] = -out[:, : len(self.pgs)].sum(axis=1)   # COM column carries the reversal
        else:
            out[:, self.com] = 0.0
        for key, value in (rule.get("fixed_cells") or {}).items():
            ij = self._parse_cell(key)
            if ij:
                out[ij] = float(value)
        return out

    def component(self, cat: str, comp: str) -> np.ndarray:
        key = (cat, comp)
        if key in self._comp:
            return self._comp[key]
        tag = f"C:{cat}|{comp}"
        self._enter(tag)
        try:
            if comp == "direct":
                val = self.direct[cat]
            elif comp == "common":
                val = self._alloc_nonpre(cat, self.cfg.com_code)
            elif comp == "corporate":
                val = self._alloc_nonpre(cat, self.cfg.cor_code)
            elif comp == "total":
                val = self.component(cat, "direct") + self.component(cat, "common") + self.component(cat, "corporate")
            else:
                raise EngineError(f"Unknown component {comp!r}")
        finally:
            self._exit(tag)
        self._comp[key] = val
        return val

    def pre_item(self, cat: str, fn: str) -> np.ndarray:
        key = (cat, fn)
        if key in self._pre_item:
            return self._pre_item[key]
        tag = f"P:{cat}|{fn}"
        self._enter(tag)
        try:
            pools = self.pre_pool.get(key, np.zeros(self.P))
            rule = self.rules.get((PRE, cat, fn))
            if rule is None:
                if np.abs(pools).sum() > 0:
                    self.alloc_log.append({"rule": f"PRE {cat} - {fn}", "leg": None, "sources": self.plants,
                                           "targets": [], "pool": float(pools.sum()), "denominator": None,
                                           "allocated": 0.0, "status": "no_rule"})
                val = self.zeros()
            else:
                val = self._run_rule(rule, pools, f"PRE {cat} - {fn}")
                val[:, self.com] = 0.0
                for key2, value in (rule.get("fixed_cells") or {}).items():
                    ij = self._parse_cell(key2)
                    if ij:
                        val[ij] = float(value)
        finally:
            self._exit(tag)
        self._pre_item[key] = val
        return val

    def _pre_ref(self, body: str) -> np.ndarray:
        parts = [p.strip() for p in body.split("|")]
        kind = parts[0].lower()
        if kind == "direct":
            if len(parts) > 1:
                cat = self.pre_cat_lookup.get(norm(parts[1]))
                return self.pre_direct.get(cat, self.zeros()) if cat else self.zeros()
            return sum((self.pre_direct[c] for c in self.cfg.pre_categories), self.zeros())
        if kind == "item":
            cat = self.pre_cat_lookup.get(norm(parts[1]))
            return self.pre_item(cat, parts[2]) if cat else self.zeros()
        if kind == "fn":
            return sum((self.pre_item(c, parts[1]) for c in self.cfg.pre_categories), self.zeros())
        if kind == "group":
            group = parts[1].lower()
            total = self.zeros()
            if group == "factory":
                total = total + self._pre_ref("direct")
            for fn, info in self.functions.items():
                if (info.get("group") or "factory").lower() == group:
                    total = total + self._pre_ref(f"fn|{fn}")
            return total
        if kind == "pool":   # unallocated pool memo (per plant) of a function, broadcast to COM column
            g = self.zeros()
            for c in self.cfg.pre_categories:
                g[:, self.com] += self.pre_pool.get((c, parts[1]), np.zeros(self.P))
            return g
        raise EngineError(f"Unknown PRE reference {body!r}")

    # ========================================================= 5. P&L lines
    def _resolve(self, ref: ex.Ref) -> np.ndarray | float:
        body, _, at = ref.body.partition("@")
        if ref.kind == "L":
            g = self.line(body)
        elif ref.kind == "C":
            cat_name, _, comp = body.partition("|")
            cat = self.cat_lookup.get(norm(cat_name))
            if cat is None:
                self.warnings.append(f"Unknown category in formula: {cat_name!r}")
                return 0.0
            g = self.component(cat, comp or "total")
        elif ref.kind == "P":
            g = self._pre_ref(body)
        elif ref.kind == "MCADJ":
            g = self.mc_adjustment_grid()
        elif ref.kind == "D":
            g = self._spec_grid({"kind": "driver", "driver": body})
        else:
            raise EngineError(f"Unknown reference kind in {ref.text}")
        return self._pick(g, at) if at else g

    def line(self, code: str) -> np.ndarray:
        if code in self._line:
            return self._line[code]
        ln = self.lines.get(code)
        if ln is None:
            raise EngineError(f"Unknown P&L line {code!r}")
        tag = f"L:{code}"
        self._enter(tag)
        try:
            val = self._as_grid(ex.evaluate(ln.get("expr") or "0", self._resolve))
            # plant- and cell-specific formula overrides (e.g. a plant with its own MC logic)
            for plant, text in (ln.get("plant_exprs") or {}).items():
                if plant in self.pidx:
                    val = val.copy()
                    val[self.pidx[plant], :] = self._as_grid(ex.evaluate(text, self._resolve))[self.pidx[plant], :]
            for key, text in (ln.get("cell_exprs") or {}).items():
                ij = self._parse_cell(key)
                if ij:
                    val = val.copy()
                    val[ij] = self._as_grid(ex.evaluate(text, self._resolve))[ij]
        finally:
            self._exit(tag)
        self._line[code] = val
        return val

    def _as_grid(self, val) -> np.ndarray:
        if isinstance(val, np.ndarray):
            return val
        return np.full((self.P, self.K), float(val))

    def is_additive(self, code: str) -> bool:
        return self.lines.get(code, {}).get("additive", True) is not False

    def aggregate_value(self, code: str, plant: str | None, pg: str | None) -> float:
        """Value of a line for one cell, a plant total, a PG across plants or the company.

        * cells come from the grid;
        * a PG across plants is the sum of its cells;
        * a plant total is the sum of the plant's cells, unless the line defines a
          ``total_exprs`` override for that plant (a total computed differently from its
          cells, as some legacy reports do) or is a ratio (``additive: false``), in which
          case the formula is evaluated on aggregated inputs;
        * the company total is the sum of plant totals (ratios: evaluated on company inputs).
        """
        key = (code, plant, pg)
        cache = self.__dict__.setdefault("_agg_cache", {})
        if key in cache:
            return cache[key]
        g = self.line(code)
        ln = self.lines[code]
        te = ln.get("total_exprs") or {}
        if plant is not None and pg is not None:
            val = float(g[self.pidx[plant], self.kidx[pg]])
        elif pg is not None:
            val = float(g[:, self.kidx[pg]].sum())
        elif plant is not None:
            if plant in te:
                val = self._eval_aggregate(te[plant], plant)
            elif self.is_additive(code):
                val = float(g[self.pidx[plant], :].sum())
            else:
                val = self._eval_aggregate((ln.get("plant_exprs") or {}).get(plant) or ln.get("expr") or "0", plant)
        else:
            if not self.is_additive(code):
                val = self._eval_aggregate(ln.get("expr") or "0", None)
            elif te:
                val = float(sum(self.aggregate_value(code, p, None) for p in self.plants))
            else:
                val = float(g.sum())
        cache[key] = val
        return val

    def _eval_aggregate(self, text: str, plant: str | None) -> float:
        def resolve(ref: ex.Ref):
            body, _, at = ref.body.partition("@")
            if at:
                return self._resolve(ref)
            if ref.kind == "L":
                return self.aggregate_value(body, plant, None)
            grid = self._resolve(ref)
            rows = slice(None) if plant is None else self.pidx[plant]
            return float(np.sum(grid[rows, :])) if isinstance(grid, np.ndarray) else float(grid)
        return float(ex.evaluate(text, resolve))

    def mc_adjustment_grid(self) -> np.ndarray:
        if hasattr(self, "_mcadj"):
            return self._mcadj
        g = self.zeros()
        sales_line = self.cfg.meta.get("sales_line")
        mc_base_line = self.cfg.meta.get("mc_tb_line")
        for key, spec in (self.inp.mc_adjustments or {}).items():
            ij = self._parse_cell(key)
            if ij is None:
                continue
            if isinstance(spec, dict) and spec.get("kind") == "mc_pct":
                if not (sales_line and mc_base_line):
                    raise EngineError("MC% adjustments need meta.sales_line and meta.mc_tb_line")
                target = float(spec["value"]) * self.line(sales_line)[ij]
                g[ij] = target - self.line(mc_base_line)[ij]
            else:
                g[ij] = float(spec["value"] if isinstance(spec, dict) else spec)
        self._mcadj = g
        return g

    def _enter(self, tag: str) -> None:
        if tag in self._stack:
            cycle = " -> ".join(self._stack[self._stack.index(tag):] + [tag])
            raise EngineError(f"Circular dependency: {cycle}")
        self._stack.append(tag)

    def _exit(self, tag: str) -> None:
        self._stack.pop()

    # ============================================================ 6. controls
    def _linear_coefficients(self, code: str) -> dict[tuple, float]:
        """Expand a line into base references with coefficients (lines are linear)."""
        memo: dict[str, dict] = {}

        def expand_node(node) -> dict:
            if isinstance(node, ex.Num):
                return {("const",): node.value}
            if isinstance(node, ex.Ref):
                if node.kind == "L":
                    return expand_line(node.body)
                if node.kind == "C":
                    cat_name, _, comp = node.body.partition("|")
                    cat = self.cat_lookup.get(norm(cat_name), cat_name)
                    comp = comp or "total"
                    if comp == "total":
                        return {("C", cat, c): 1.0 for c in ("direct", "common", "corporate")}
                    return {("C", cat, comp): 1.0}
                return {(node.kind, node.body): 1.0}
            if isinstance(node, ex.UnaryNeg):
                return {k: -v for k, v in expand_node(node.operand).items()}
            if isinstance(node, ex.BinOp):
                a, b = expand_node(node.left), expand_node(node.right)
                if node.op in "+-":
                    out = dict(a)
                    for k, v in b.items():
                        out[k] = out.get(k, 0.0) + (v if node.op == "+" else -v)
                    return out
                # multiplication / division only by constants keeps linearity
                if node.op == "*" and set(b) == {("const",)}:
                    return {k: v * b[("const",)] for k, v in a.items()}
                if node.op == "*" and set(a) == {("const",)}:
                    return {k: v * a[("const",)] for k, v in b.items()}
                if node.op == "/" and set(b) == {("const",)} and b[("const",)] != 0:
                    return {k: v / b[("const",)] for k, v in a.items()}
                return {("nonlinear",): 1.0}
            if isinstance(node, ex.Call):
                if node.name == "SUM":
                    out: dict = {}
                    for arg in node.args:
                        for k, v in expand_node(arg).items():
                            out[k] = out.get(k, 0.0) + v
                    return out
                if node.name == "NEG":
                    return {k: -v for k, v in expand_node(node.args[0]).items()}
                return {("nonlinear",): 1.0}
            return {("nonlinear",): 1.0}

        def expand_line(c: str) -> dict:
            if c not in memo:
                ln = self.lines.get(c)
                memo[c] = expand_node(ex.parse(ln.get("expr") or "0")) if ln else {}
            return memo[c]

        return expand_line(code)

    def controls(self, ba_raw: pd.DataFrame, ba_adj: pd.DataFrame, cc_adj: pd.DataFrame,
                 np_facts: pd.DataFrame, pre_facts: pd.DataFrame) -> list[Control]:
        out: list[Control] = []
        div = self.div

        # --- TB tie-out: every BA-TB rupee is either used, excluded or flagged
        by_status = np_facts.groupby("status")["amount"].sum() / div
        used = float(by_status.get("ok", 0.0))
        pre_in_ba = float(by_status.get("pre_gl", 0.0))
        excluded = float(by_status.get("excluded_gl", 0.0))
        problems = {k: float(v) for k, v in by_status.items() if k not in ("ok", "pre_gl", "excluded_gl")}
        total = float(ba_adj["amount"].sum() / div)
        out.append(Control(
            "tb_tieout", "ok" if abs(total - used - pre_in_ba - excluded - sum(problems.values())) < TOL else "error",
            "Business-area TB tie-out (used + PRE + excluded + flagged = TB total)", total,
            [{"item": "TB total after adjustments", "amount": total},
             {"item": "TB total before adjustments", "amount": float(ba_raw["amount"].sum() / div)},
             {"item": "Used in Non-PRE", "amount": used},
             {"item": "PRE GLs (taken from cost-centre TB)", "amount": pre_in_ba},
             {"item": "Excluded GLs", "amount": excluded}]
            + [{"item": f"Flagged: {k}", "amount": v} for k, v in problems.items()]))

        # --- Unmapped / flagged amounts (Non-PRE)
        for status, title in [("unmapped_gl", "GL accounts missing from the GL master"),
                              ("unmapped_ba", "Business areas missing from the BA master"),
                              ("category_not_in_pnl", "GL categories not used by the P&L structure"),
                              ("outside_plants", "Business areas of plants outside the report"),
                              ("unreported_code", "Business areas whose PG/COM/COR code is not reported")]:
            rows = np_facts[np_facts["status"] == status]
            amt = float(rows["amount"].sum() / div)
            key = "gl" if status in ("unmapped_gl", "category_not_in_pnl") else "ba"
            det = (rows.groupby([key] + (["category_raw"] if status == "category_not_in_pnl" else []))["amount"]
                   .sum().div(div).sort_values(key=np.abs, ascending=False).head(50))
            details = [{"key": " / ".join(map(str, k)) if isinstance(k, tuple) else str(k), "amount": float(v)}
                       for k, v in det.items()]
            level = "ok" if rows.empty else ("warning" if abs(amt) < TOL and status != "unmapped_gl" else "error")
            if rows.empty or (abs(rows["amount"]).sum() / div) < TOL:
                level = "ok" if rows.empty else "warning"
            out.append(Control(f"np_{status}", level, title, amt, details))

        # --- PRE checks
        pre_by = pre_facts.groupby("status")["amount"].sum() / div
        pre_ok = float(pre_by.get("ok", 0.0))
        for status, title in [("unmapped_cc", "Cost centres missing from the cost-centre master"),
                              ("unknown_function", "Cost centres with an unknown function"),
                              ("outside_plants", "Cost centres of plants outside the report"),
                              ("category_not_in_pnl", "PRE GLs with an unknown PRE category")]:
            rows = pre_facts[pre_facts["status"] == status]
            amt = float(rows["amount"].sum() / div)
            det = rows.groupby("cc")["amount"].sum().div(div).sort_values(key=np.abs, ascending=False).head(50)
            out.append(Control(f"pre_{status}", "ok" if rows.empty else "error", title, amt,
                               [{"key": str(k), "amount": float(v)} for k, v in det.items()]))
        cc_pre_total = float(pre_facts.loc[pre_facts["gl_type"] == PRE, "amount"].sum() / div)
        diff = cc_pre_total - pre_in_ba
        out.append(Control("pre_cross_check", "ok" if abs(diff) < TOL else "warning",
                           "PRE GLs: cost-centre TB vs business-area TB", diff,
                           [{"item": "Cost-centre TB", "amount": cc_pre_total},
                            {"item": "Business-area TB", "amount": pre_in_ba}]))

        # --- Allocation in = out
        lost = []
        for e in self.alloc_log:
            if e["status"] in ("no_rule", "zero_or_invalid_basis"):
                lost.append(e)
            elif e["status"] == "ok" and abs(e["pool"] - e["allocated"]) > TOL:
                lost.append(e)
        lost_amt = float(sum(e["pool"] - e["allocated"] for e in lost))
        out.append(Control("allocation_balance", "ok" if not lost else "warning",
                           "Allocations: pool amount = allocated amount", lost_amt,
                           [{"key": f"{e['rule']} (leg {e['leg']})", "status": e["status"], "pool": e["pool"],
                             "allocated": e["allocated"], "amount": e["pool"] - e["allocated"]} for e in lost]))
        plugs = [e for e in self.alloc_log if e["status"] == "plug"]
        if plugs:
            out.append(Control("allocation_plugs", "warning", "Manual allocation plugs (fixed amounts inside rules)",
                               float(sum(e["allocated"] for e in plugs)),
                               [{"key": f"{e['rule']} @ {e['cell']}", "amount": e["allocated"]} for e in plugs]))

        # --- MC adjustments impact
        mc = self.mc_adjustment_grid()
        if np.abs(mc).sum() > 0:
            out.append(Control("mc_adjustments", "ok" if abs(mc.sum()) < TOL else "warning",
                               "Material-cost adjustments (net effect on company PBT)", float(-mc.sum()),
                               [{"key": cell_key(self.plants[i], self.codes[j]), "amount": float(mc[i, j])}
                                for i, j in zip(*np.nonzero(mc))]))

        # --- PBT coverage: each category must reach PBT exactly once with the right sign
        pbt = self.cfg.meta.get("pbt_line")
        if pbt and pbt in self.lines:
            coef = self._linear_coefficients(pbt)
            issues = []
            for c in self.cfg.categories:
                name = c["name"]
                expected = -self.cat_sign[name]   # profit = -(sum of TB amounts)
                for comp in ("direct", "common", "corporate"):
                    got = coef.get(("C", name, comp), 0.0)
                    if comp == "direct":
                        amount = float(self.direct[name].sum())
                    else:
                        amount = float(self.component(name, comp).sum())
                    if abs(got - expected) > 1e-9 and abs(amount) > TOL:
                        issues.append({"key": f"{name} [{comp}]", "coefficient": got, "expected": expected,
                                       "amount": amount, "effect": (got - expected) * amount})
            # PRE must reach PBT with coefficient -1
            for cat in self.cfg.pre_categories:
                for (pc, fn), pool in self.pre_pool.items():
                    if pc != cat:
                        continue
                    got = coef.get(("P", f"fn|{fn}"), 0.0) + coef.get(("P", f"item|{cat}|{fn}"), 0.0)
                    got += sum(v for k, v in coef.items() if k[0] == "P" and k[1].startswith("group|")
                               and (self.functions.get(fn, {}).get("group", "factory") == k[1].split("|")[1]))
                    amount = float(self.pre_item(cat, fn).sum())
                    if abs(got + 1.0) > 1e-9 and abs(amount) > TOL:
                        issues.append({"key": f"PRE {cat} - {fn}", "coefficient": got, "expected": -1.0,
                                       "amount": amount, "effect": (got + 1.0) * amount})
            effect = float(sum(i["effect"] for i in issues))
            out.append(Control("pbt_coverage", "ok" if not issues else "warning",
                               "Every category reaches PBT exactly once with the right sign", effect, issues))

            # --- PBT vs TB
            pbt_val = float(self.line(pbt).sum())
            tb_profit = -float((np_facts.loc[np_facts["status"] == "ok", "amount"].sum()
                                + pre_facts.loc[pre_facts["status"] == "ok", "amount"].sum()) / div)
            out.append(Control("pbt_vs_tb", "ok" if abs(pbt_val - tb_profit) < TOL else "warning",
                               "Company PBT per P&L vs profit per TB (mapped amounts)", pbt_val - tb_profit,
                               [{"item": "PBT per P&L", "amount": pbt_val},
                                {"item": "Profit per TB (mapped GLs)", "amount": tb_profit}]))
        return out

    # ================================================================ run
    def run(self) -> "EngineResult":
        t0 = time.perf_counter()
        ba = normalise_tb(self.inp.ba_tb, "ba")
        cc = normalise_tb(self.inp.cc_tb, "cc")
        ba_adj, cc_adj = self.apply_adjustments(ba, cc)
        t1 = time.perf_counter()
        np_facts, pre_facts = self.tag(ba_adj, cc_adj)
        t2 = time.perf_counter()
        self.aggregate(np_facts, pre_facts)
        t3 = time.perf_counter()
        for c in self.cfg.categories:
            for comp in COMPONENTS:
                self.component(c["name"], comp)
        for ln in self.cfg.lines:
            self.line(ln["code"])
        t4 = time.perf_counter()
        ctrls = self.controls(ba, ba_adj, cc_adj, np_facts, pre_facts)
        t5 = time.perf_counter()
        self.timings = {"adjust": t1 - t0, "tag": t2 - t1, "aggregate": t3 - t2,
                        "allocate_and_pnl": t4 - t3, "controls": t5 - t4, "total": t5 - t0}
        return EngineResult(self, np_facts, pre_facts, ctrls)


class EngineResult:
    """Read-only view over a finished calculation."""

    def __init__(self, engine: CostingEngine, np_facts: pd.DataFrame, pre_facts: pd.DataFrame,
                 controls: list[Control]):
        self.engine = engine
        self.cfg = engine.cfg
        self.plants = engine.plants
        self.codes = engine.codes
        self.np_facts = np_facts
        self.pre_facts = pre_facts
        self.controls = controls
        self.lines = {code: engine._line[code] for code in engine._line}
        self.components = dict(engine._comp)
        self.pre_items = dict(engine._pre_item)
        self.alloc_log = engine.alloc_log
        self.adjust_log = engine.adjust_log
        self.timings = engine.timings
        self.warnings = sorted(set(engine.warnings))

    # values ------------------------------------------------------------
    def value(self, line: str, plant: str | None = None, code: str | None = None) -> float:
        """Cell value, plant total (code=None), PG across plants (plant=None) or company total."""
        if code == "__TOTAL__":
            code = None
        if plant == "__ALL__":
            plant = None
        return self.engine.aggregate_value(line, plant, code)

    def cor_memo(self, line: str) -> np.ndarray:
        """Corporate (COR) amounts by plant before pooling, for the lines that use them."""
        e = self.engine

        def resolve(ref: ex.Ref):
            if ref.kind == "L":
                return self.cor_memo(ref.body)
            if ref.kind == "C":
                cat_name, _, comp = ref.body.partition("|")
                cat = e.cat_lookup.get(norm(cat_name))
                if cat and (comp or "total") in ("direct", "total"):
                    return e.cor_memo[cat]
            return np.zeros(e.P)

        val = ex.evaluate(e.lines[line].get("expr") or "0", resolve)
        return val if isinstance(val, np.ndarray) else np.full(e.P, float(val))

    def control_summary(self) -> dict:
        levels = [c.level for c in self.controls]
        return {"errors": levels.count("error"), "warnings": levels.count("warning"), "ok": levels.count("ok")}

    def long_table(self) -> pd.DataFrame:
        """Long format results incl. plant and company totals (code '__TOTAL__')."""
        rows = []
        e = self.engine
        for code, g in self.lines.items():
            for i, p in enumerate(self.plants):
                for j, c in enumerate(self.codes):
                    if g[i, j] != 0.0:
                        rows.append((code, p, c, float(g[i, j])))
                rows.append((code, p, "__TOTAL__", e.aggregate_value(code, p, None)))
            for j, c in enumerate(self.codes):
                rows.append((code, "__ALL__", c, e.aggregate_value(code, None, c)))
            rows.append((code, "__ALL__", "__TOTAL__", e.aggregate_value(code, None, None)))
        return pd.DataFrame(rows, columns=["line", "plant", "code", "value"])

    # drill-down -----------------------------------------------------------
    def explain(self, line: str, plant: str, code: str) -> list[dict]:
        """Break a P&L cell into base components (category x component, PRE items, MC adjustment)."""
        e = self.engine
        coef = e._linear_coefficients(line)
        i, j = e.pidx[plant], e.kidx[code] if code != "__TOTAL__" else None
        out = []
        for key, c in coef.items():
            if key[0] in ("const", "nonlinear") or c == 0:
                continue
            if key[0] == "C":
                g = e.component(key[1], key[2])
                label = f"{key[1]} – {key[2]}"
            elif key[0] == "P":
                g = e._pre_ref(key[1])
                label = f"PRE {key[1]}"
            elif key[0] == "MCADJ":
                g = e.mc_adjustment_grid()
                label = "Material-cost adjustment"
            elif key[0] == "D":
                g = e._spec_grid({"kind": "driver", "driver": key[1]})
                label = f"Driver {key[1]}"
            else:
                continue
            v = float(g[i, :].sum() if j is None else g[i, j]) * c
            if abs(v) > 0:
                out.append({"ref": key, "label": label, "coefficient": c, "value": v})
        return sorted(out, key=lambda r: -abs(r["value"]))

    def source_rows(self, category: str, component: str, plant: str, code: str) -> pd.DataFrame:
        """GL x BA rows behind a component cell.  Allocated components are shown as the
        pool's GL rows scaled by the share that reached this cell."""
        e = self.engine
        f = self.np_facts[(self.np_facts["status"] == "ok") & (self.np_facts["category"] == category)]
        sign = e.cat_sign.get(category, 1)
        if component == "direct":
            rows = f[(f["plant"] == plant) & (f["code"] == code)].copy()
            rows["value"] = sign * rows["amount"] / e.div
            rows["share"] = 1.0
            return rows
        pool_code = e.cfg.com_code if component == "common" else e.cfg.cor_code
        rows = f[f["code"] == pool_code].copy()
        comp_val = float(e.component(category, component)[e.pidx[plant], e.kidx[code]])
        pool_total = float(sign * rows["amount"].sum() / e.div)
        share = comp_val / pool_total if pool_total else 0.0
        rows["value"] = sign * rows["amount"] / e.div * share
        rows["share"] = share
        return rows
