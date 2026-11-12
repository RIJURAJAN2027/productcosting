# Calculation engine – reference

This is how a calculation run turns the two trial balances into plant-wise and product-group-wise
P&L reports, and how every rule is configured. The engine lives in `backend/app/engine/`
(`core.py`, `config.py`, `expr.py`); it is pure Python / NumPy and has no database dependency, so the
same code runs inside the application, in the tests and in `python -m app.cli verify-workbook`.

```
 BA trial balance ─┐                                                       ┌─ P&L lines (formulas)
 (GL × business    │   1 adjust   2 tag      3 aggregate    4 allocate      │   plant × PG grid
  area)            ├──► rules ──► masters ──► plant × PG ──► COM / COR / ───┤
 CC trial balance ─┘   (audited)  (BA, GL,    × category     PRE pools      └─ 6 controls
 (GL × cost centre)               CC, fn)                                       (in = out, tie-outs)
```

## 0. Units and signs

* Uploaded amounts are rupees in TB sign (debit +, credit −). Uploads are stored unchanged.
* The engine works in a *calculation unit* (`calc_unit_divisor`, default 1,00,00,000 = crore) so
  formula constants and fixed amounts in rules read like the legacy workbook. Reports can be shown in
  rupees, thousands, lakh, million or crore; the conversion is exact (no intermediate rounding).
* Each category has a sign (`1` or `−1`). Revenue categories normally have sign −1 so credits show
  as positive sales; cost categories have sign 1.

## 1. Adjustment rules

Adjustments change the TBs *before* tagging and are applied in their sort order. Each has a type, a
reason (mandatory), an author and a scope (one period or standing for every period). They never change
the stored upload; the run keeps a snapshot of the rules it used.

| Type | Definition | Effect |
|---|---|---|
| `regroup_gl` | `from_gls`, `to_gl` | Amounts of the source GLs are booked to the target GL |
| `move_dim` | `gl`, `to`, optional `from` (list) | Amounts of a GL move to another business area / cost centre |
| `exclude_gl` | `gls`, optional `dims` | Rows are removed (reported in the tie-out as excluded) |
| `amount` | `gl`, `dim`, `amount` (rupees) | A fixed correction is added |
| `reclass` | `from {gl, dim}`, `to {gl?, dim}`, `amount` | Moves an amount between cells |

`tb` selects the trial balance: `BA` (default), `CC` or `BOTH`.

## 2. Tagging

Every BA-TB row gets a GL type (Non-PRE, PRE or excluded), a category, a plant and a code (product
group, `COM` for plant-common or `COR` for corporate) from the masters valid on the period end date.
Every CC-TB row of a PRE GL gets a plant and a *function* from the cost-centre master.

Rows that cannot be tagged are **not dropped**; they get a status and appear in the controls:
`unmapped_gl`, `unmapped_ba`, `category_not_in_pnl`, `outside_plants`, `unreported_code` (Non-PRE)
and `unmapped_cc`, `unknown_function`, `outside_plants`, `category` (PRE).

PRE (people-related expenses: wages, salaries, welfare …) is taken from the cost-centre TB because
only the cost centre says which function the people work for. The BA TB's PRE GLs are used to
cross-check the CC TB (`pre_cross_check`).

## 3. Aggregation

Tagged Non-PRE rows are summed into a grid per category: **plants × codes** where codes are the
product groups followed by `COM`. `COR` amounts are kept per plant as a memo until the corporate
pool is allocated. Each category therefore has three *components*:

* `direct` – booked straight to a product group (or to COM before allocation)
* `common` – the plant's COM pool allocated to its product groups (the COM column gets −pool,
  so plant totals do not change)
* `corporate` – the COR pool allocated (by default company-wide)

`[C:Category|total]` is direct + common + corporate.

## 4. Allocation rules

One rule per *dataset × category × pool*: dataset `NONPRE` or `PRE`, pool `COM`, `COR` or a PRE
function code. A rule with no legs uses its *mode*:

* `plant` – each plant's pool is spread over that plant's product groups;
* `pooled` – the pools of all plants are added and spread over all plants' product groups;
* `custom` – explicit `legs`.

For each leg: **share of a cell = pool × basis(cell) ÷ denominator**. Shares go to product-group
cells only; the denominator by default is the sum of the basis over every cell of the target plants
(including COM, as the legacy workbook does – so a basis value on COM leaves part of the pool
unallocated, which the `allocation_balance` control reports).

### Rule definition (JSON, stored in `allocation_rules.definition`)

```json
{
  "basis": {"kind": "line", "line": "sales_total"},
  "basis_cells": {"NTH|SPCL": {"kind": "const", "value": 0}},
  "legs": [
    {"sources": ["NTH", "STH"], "targets": ["NTH", "STH"],
     "basis": {"kind": "component", "category": "Power & Fuel", "component": "direct"},
     "denominator": {"kind": "basis", "plants": ["NTH", "STH"], "extra": 0}}
  ],
  "plugs": {"WST|GATE": 0.25},
  "fixed_cells": {"NTH|BALL": 0}
}
```

Basis kinds (`basis`, `basis_cells` values, `sum.items`):

| kind | parameters | value per cell |
|---|---|---|
| `line` | `line` | a P&L line (e.g. sales, material cost) – may itself be after allocation |
| `component` | `category`, `component` (`direct`/`common`/`corporate`/`total`), `fallback` | a category part |
| `pre` | `ref` (e.g. `direct`, `direct|Wages`, `fn|QC`) | PRE amounts |
| `driver` | `driver` | quantities entered per period (machine hours, kWh, headcount …) |
| `const` | `value` | the same value everywhere (equal split) |
| `sum` | `items` (list of specs) | sum of several bases |
| `none` | – | zero |
| `error` | – | marks a cell whose legacy formula was an error; the leg is reported, not allocated |

Every spec accepts `at` (`"PLANT|CODE"`, `CODE` may be `__TOTAL__`, `PLANT` may be `__ALL__`) to use
one cell's or one total's value everywhere, and `scale` to multiply it.

Denominator (`legs[].denominator`): `kind` `basis` (default; optional `plants` or `cells`),
`line_total` (`line`, optional `plants`) or `const` (`value`); `extra` adds a fixed amount (a number
or `{plant: amount}`).

`plugs` add fixed amounts to cells after the allocation (the legacy workbook has a few hard-coded
balancing figures; they are reported in the `allocation_plugs` control). `fixed_cells` (PRE rules)
override a cell's allocated value.

Allocation is evaluated lazily: a basis can refer to a line that needs other allocations first; the
engine resolves the order itself and stops with a clear message if rules depend on each other in a
circle.

## 5. P&L lines – formula language

Every P&L line has a code, a label and a formula. Formulas are parsed by a small, safe expression
parser (no `eval`).

| Reference | Meaning |
|---|---|
| `[L:code]` | another line |
| `[C:Category\|component]` | a category part: `direct`, `common`, `corporate` or `total` |
| `[P:direct]`, `[P:direct\|Wages]` | PRE booked directly to product-group cost centres |
| `[P:item\|Wages\|QC]`, `[P:fn\|QC]` | PRE of a function after its allocation (one category / all) |
| `[P:group\|factory]`, `[P:group\|corporate]` | all PRE of a group (factory includes direct PRE) |
| `[P:pool\|QC]` | unallocated pool of a function, by plant (memo) |
| `[MCADJ]` | material-cost adjustments (amount, or target MC % of sales) |
| `[D:driver]` | a driver's values |
| `…@PLANT\|CODE` | pins any reference to one cell or total, e.g. `[L:sales@NTH\|__TOTAL__]` |

Operators `+ − * /` and parentheses; functions `SUM`, `NEG`, `ABS`, `MIN`, `MAX`. Division by zero
gives 0.

Line attributes (`pnl_lines.attrs`):

* `plant_exprs: {PLANT: formula}` and `cell_exprs: {"PLANT|PG": formula}` override the formula for
  one plant or one cell (the legacy workbook has several hand-edited cells; the importer keeps them).
* `total_exprs: {PLANT: formula}` – a plant total computed differently from the sum of its cells.
* `additive: false` – a ratio (e.g. break-even sales); totals are evaluated on aggregated inputs, not
  summed.
* `pct_base` – the line used for "% of sales" (default: the sales line).
* `hidden: true` – calculated and drillable but not shown unless a report layout lists it.

Report layouts (`report_templates`) list the rows of each report (PG-wise, plant-wise, PG totals,
all PG) in order, with labels and bold / fill styles, so the output keeps the workbook's look.

## 6. Controls

Every run stores these checks; errors and warnings are shown before submission and in the approval
screen.

| Code | Checks |
|---|---|
| `tb_tieout` | BA-TB total = Non-PRE used + PRE + excluded + flagged (nothing silently lost) |
| `np_unmapped_gl`, `np_unmapped_ba` | GLs / business areas missing from the masters |
| `np_category_not_in_pnl` | amounts on categories that no P&L line uses |
| `np_outside_plants`, `np_unreported_code` | business areas of plants or codes not in the report |
| `pre_unmapped_cc`, `pre_unknown_function`, `pre_outside_plants`, `pre_category` | the same for PRE |
| `pre_cross_check` | PRE per CC TB = PRE per BA TB |
| `allocation_balance` | for every leg: pool = allocated (a zero or invalid basis is reported) |
| `allocation_plugs` | fixed amounts inside rules |
| `mc_adjustments` | net effect of material-cost adjustments on company PBT |
| `pbt_coverage` | every category reaches PBT exactly once with the right sign |
| `pbt_vs_tb` | company PBT per P&L = profit per TB (mapped amounts) |

## 7. Drill-down

Any amount in a report can be explained: the engine expands the line's formula for that cell (or
plant total), following plant / cell / total overrides and pinned references, into category parts,
PRE items, material-cost adjustments and drivers. Each part lists the GL × business-area (or cost
centre) rows behind it; allocated parts show the pool's rows scaled by the share that reached the
cell. Parts always add up to the reported value, and each part's rows add up to the part.

## 8. Performance

A full calculation on a real-world configuration (several hundred GLs, business areas and cost
centres, about two hundred allocation rules and a few hundred P&L lines) takes well under a second,
including storing every value of the run. The automated test `test_performance_1000_gls_900_columns`
runs 1,000 GLs × 900 columns in well under the 30-second requirement.
