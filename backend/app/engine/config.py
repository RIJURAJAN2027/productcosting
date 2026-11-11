"""Engine configuration: masters, allocation rules and the P&L line structure.

The configuration is plain JSON-compatible data so it can be stored in the
database (versioned by effective date), edited in the UI, exported to Excel and
imported from a legacy workbook.  ``EngineConfig.validate`` reports problems
before a run starts.

Key concepts
------------
* **Plant x code grid** – every result is a matrix with one row per reporting
  plant and one column per product group (PG) plus the *Common* (COM) column.
  Corporate (COR) amounts are kept as a per-plant memo until they are pooled.
* **Category** – a cost/revenue line (e.g. "Power & Fuel").  GL accounts map to
  categories.  ``sign = -1`` flips credits so income shows as positive.
* **Components** – for every Non-PRE category the engine produces ``direct``,
  ``common`` (allocated COM pool), ``corporate`` (allocated COR pool) and
  ``total``.
* **Allocation rule** – how a pool is spread over product groups:
  ``share(cell) = pool x basis(cell) / denominator``.  Bases can reference
  components, P&L lines, PRE results, manual drivers or constants, and can be
  overridden per cell.
* **PRE** – personnel cost from the cost-centre TB.  A cost centre's *function*
  is either a PG code (direct) or a service function (QC, Stores, ...) that is
  allocated within the plant (``mode=plant``) or across plants (``pooled``).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from . import expr as ex

NONPRE = "NONPRE"
PRE = "PRE"
EXCLUDED = "EXCLUDED"
GL_TYPES = (NONPRE, PRE, EXCLUDED)
COMPONENTS = ("direct", "common", "corporate", "total")
BASIS_KINDS = ("component", "line", "pre", "const", "sum", "driver", "none", "error")


def norm(text: Any) -> str:
    """Case/space-insensitive key used for matching names (Excel lookups are case-insensitive)."""
    return " ".join(str(text).split()).casefold() if text is not None else ""


def code_str(value: Any) -> str:
    """Normalise GL / cost-centre / BA codes coming from Excel (50000.0 -> '50000')."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def cell_key(plant: str, code: str) -> str:
    return f"{plant}|{code}"


def split_cell_key(key: str) -> tuple[str, str]:
    plant, _, code = key.partition("|")
    return plant, code


@dataclass
class EngineConfig:
    plants: list[str]
    product_groups: list[str]
    com_code: str = "COM"
    cor_code: str = "COR"
    calc_unit_divisor: float = 1e7
    business_areas: dict[str, dict] = field(default_factory=dict)   # ba -> {plant, code}
    gl_accounts: dict[str, dict] = field(default_factory=dict)      # gl -> {category, type, description}
    cost_centres: dict[str, dict] = field(default_factory=dict)     # cc -> {plant, function, barea}
    categories: list[dict] = field(default_factory=list)            # [{name, sign}]
    pre_categories: list[str] = field(default_factory=lambda: ["Wages", "Salaries", "Others"])
    functions: dict[str, dict] = field(default_factory=dict)        # code -> {group, mode}
    rules: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)                 # [{code, label, expr, ...}]
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    @property
    def codes(self) -> list[str]:
        """Grid columns: product groups followed by the Common column."""
        return list(self.product_groups) + [self.com_code]

    def category_lookup(self) -> dict[str, str]:
        return {norm(c["name"]): c["name"] for c in self.categories}

    def line_lookup(self) -> dict[str, dict]:
        return {ln["code"]: ln for ln in self.lines}

    def line_by_label(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for ln in self.lines:
            out.setdefault(norm(ln["label"]), ln["code"])
        return out

    # ------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EngineConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    # -------------------------------------------------------------- validation
    def validate(self) -> list[dict]:
        """Return a list of {level, message} problems (level: error|warning)."""
        problems: list[dict] = []

        def err(msg: str) -> None:
            problems.append({"level": "error", "message": msg})

        def warn(msg: str) -> None:
            problems.append({"level": "warning", "message": msg})

        if not self.plants:
            err("No reporting plants configured.")
        if not self.product_groups:
            err("No product groups configured.")
        if len(set(self.plants)) != len(self.plants):
            err("Duplicate plant codes.")
        if len(set(self.product_groups)) != len(self.product_groups):
            err("Duplicate product group codes.")
        if self.calc_unit_divisor <= 0:
            err("Calculation unit divisor must be positive.")
        cats = self.category_lookup()
        if len(cats) != len(self.categories):
            err("Duplicate category names (names are case-insensitive).")
        for c in self.categories:
            if c.get("sign") not in (1, -1):
                err(f"Category {c['name']!r}: sign must be 1 or -1.")
        for gl, info in self.gl_accounts.items():
            if info.get("type") not in GL_TYPES:
                err(f"GL {gl}: type must be one of {GL_TYPES}.")
            elif info["type"] == NONPRE and norm(info.get("category")) not in cats:
                warn(f"GL {gl} maps to category {info.get('category')!r} which is not in the P&L category list; "
                     "its amount will be reported as unmapped.")
            elif info["type"] == PRE and norm(info.get("category")) not in {norm(c) for c in self.pre_categories}:
                warn(f"GL {gl} maps to PRE category {info.get('category')!r} which is not a PRE category.")
        seen = set()
        line_codes = {ln["code"] for ln in self.lines}
        for r in self.rules:
            key = (r.get("dataset"), norm(r.get("category")), r.get("pool"))
            if key in seen:
                err(f"Duplicate allocation rule for {key}.")
            seen.add(key)
            if r.get("dataset") == NONPRE and norm(r.get("category")) not in cats:
                warn(f"Allocation rule for unknown category {r.get('category')!r} will be ignored.")
            for spec in _iter_specs(r):
                if spec.get("kind") not in BASIS_KINDS:
                    err(f"Rule {r.get('category')}/{r.get('pool')}: unknown basis kind {spec.get('kind')!r}.")
                if spec.get("kind") == "line" and spec.get("line") not in line_codes:
                    err(f"Rule {r.get('category')}/{r.get('pool')}: basis line {spec.get('line')!r} does not exist.")
        codes_seen = set()
        for ln in self.lines:
            if ln["code"] in codes_seen:
                err(f"Duplicate line code {ln['code']!r}.")
            codes_seen.add(ln["code"])
            try:
                node = ex.parse(ln.get("expr") or "0")
            except ex.ExpressionError as e:  # pragma: no cover - surfaced to UI
                err(f"Line {ln['code']}: {e}")
                continue
            for ref in ex.refs(node):
                if ref.kind == "L" and ref.body not in line_codes:
                    err(f"Line {ln['code']} references unknown line {ref.body!r}.")
                if ref.kind == "C":
                    cat, _, comp = ref.body.partition("|")
                    if norm(cat) not in cats:
                        warn(f"Line {ln['code']} references unknown category {cat!r} (treated as 0).")
                    if comp and comp not in COMPONENTS:
                        err(f"Line {ln['code']}: unknown component {comp!r}.")
        return problems


def _iter_specs(rule: dict):
    def walk(spec):
        if not spec:
            return
        yield spec
        for item in spec.get("items", []) or []:
            yield from walk(item)
    yield from walk(rule.get("basis"))
    for spec in (rule.get("basis_cells") or {}).values():
        yield from walk(spec)
    for leg in rule.get("legs") or []:
        yield from walk(leg.get("basis"))
