# Design decisions and open questions

The software was built from the legacy *Product costing – plant wise* workbook. These are the
assumptions it is built on and the questions about the allocation rules that the business should
confirm. Company-specific figures and codes are deliberately not recorded here.

## How the legacy workbook is reproduced

* Every formula of the calculation sheets is translated into configuration – allocation rules, P&L
  lines with plant / cell / total overrides, report layouts – so the result matches the workbook to
  the rupee (the regression test compares every value; tolerance ₹1).
* Hand-typed numbers in the workbook (allocation percentages, balancing plugs, zeroed cells, edited
  amounts) are kept, but made visible: typed bases become basis values on the rule, plugs appear in
  the *manual allocation plugs* control, and edits to the trial balance become **adjustment rules
  with a reason**.
* Workbook defects are reproduced, not silently fixed, and each one is reported by a control – for
  example GL categories that no P&L line uses (amounts that disappear from the P&L), allocation legs
  whose basis leaves part of a pool unallocated, and the difference between the P&L's PBT and the
  profit per trial balance. Fixing them is a configuration change (map the category, remove the plug,
  correct the basis) that the business signs off as a new baseline.

## Assumptions

**Data**
1. Each upload is a trial balance of one company for one period, normally year-to-date. A month is
   derived as YTD(n) − YTD(n−1) (*Derive monthly*); allocations run on whichever view is calculated.
2. The importer finds the header row (G/L code and Total columns), ignores rows without a GL code
   and total rows, and matches columns by header name; helper columns can be listed per company.
3. People cost (PRE) comes from the cost-centre TB only; the same GLs in the business-area TB are used
   to cross-check it.
4. Manual corrections are adjustment rules with period, reason and author – never edits of the
   uploaded file.

**Masters**
5. Business area → plant / product group / COM / COR, cost centre → plant / function and GL →
   category / type always come from the masters (effective-dated), never from code patterns.
6. Plants, product groups, functions, categories and the P&L structure are per-company
   configuration, not code.

**Engine**
7. Order of calculation is derived from the dependencies (a Corporate basis can be a post-Common
   total; PRE can be allocated on sales after material-cost adjustments); circular rules are refused.
8. Every allocation basis is a definition (a line, a category part, PRE, a driver table entered per
   period, a constant or a sum), so it can be audited and changed without code.
9. A zero, missing or invalid basis does not drop money silently: the allocation leg is skipped and
   the *allocations: pool = allocated* control shows the plant, rule and amount.
10. Calculations use full floating-point precision; rounding happens only for display and exports.
11. Material cost can be corrected per plant × product group to the worked-out figure, as an amount
    or as a target MC % of sales; the MC % report shows the TB and the worked-out figures.

## Questions to confirm on the allocation rules

1. **Corporate pool** – should corporate costs of every plant be pooled and spread over the
   producing plants' product groups? Should plants without sales appear as report columns?
2. **Plants outside the report** – if business areas of non-reported plants carry amounts in a
   period, should they be reported, pooled into Corporate, or only flagged (current behaviour)?
3. **Traded goods** – should traded product groups take Common and Corporate allocations, or only
   their direct costs?
4. **Typed bases** – which typed-in percentages or quantities are really drivers (machine hours, power
   units, headcount, floor area) that should be uploaded each period?
5. **Common vs Corporate bases** – Common on the plant's direct amounts, Corporate on post-Common
   totals, material cost after the worked-out adjustment: confirm.
6. **PRE functions** – which functions are allocated within their plant and which are pooled as
   corporate? Are functions whose wages and salaries sit in different groups intended?
7. **Domestic / export functions** – allocate only to product groups with that kind of sale, or to
   all?
8. **Negative bases** (e.g. net negative sales after returns and liquidated damages) – should such a
   product group still receive a share?
9. **Workbook defects** – are the balancing plugs, unmapped categories and edited cells deliberate
   management adjustments (keep as rules with reasons) or errors (fix and sign off a new baseline)?
10. **Standing exclusions** – are excluded amounts (e.g. foreign-exchange items) a standing policy or a
    one-off for one period?
11. **Material cost** – confirm the worked-out MC rule (TB MC + adjustment, or target MC % × net
    sales) and its sign convention, and which product groups use it.
12. **MIS reconciliation** – which MIS lines are compared, in which sign, and what tolerance counts as
    a difference?
13. **Budgets** – will budgets come as trial balances (same engine) or directly at plant × product
    group × P&L-line level?
14. **Legal entities** – is one upload always one company code?
