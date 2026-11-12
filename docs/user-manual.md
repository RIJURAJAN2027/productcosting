# User manual

For costing analysts, approvers and report users. Administrators: see the
[admin guide](admin-guide.md) for installation, users and settings.

## Roles

| Role | Can |
|---|---|
| **Viewer** | read dashboards, reports, drill-downs and the audit trail; export Excel / PDF |
| **Costing analyst** | everything a viewer can, plus uploads, masters, adjustments, calculations and submitting runs for approval |
| **Approver** | everything a viewer can, plus approving / rejecting / publishing runs and locking periods |
| **Admin** | everything, plus users, companies, settings, backup / restore and unlocking periods |

Calculations follow **maker-checker**: the person who submits a run cannot approve or publish it.

## The screen

* The top bar selects the **company**, the **period** and the **display unit** (₹, thousand, lakh,
  million, crore). Your choices are remembered on this browser.
* The left menu holds the pages below. On a phone the menu sits at the top.
* *Theme* switches between light, dark and your system setting.

## Monthly process

### 1. Create the period

*Periods & uploads → New period.* A period has a code (e.g. `2026-10`), a label (e.g. *YTD Apr–Oct
2026*), a scenario (actual, budget or forecast), the basis (YTD or month) and its dates. The number of
months is used for break-even sales per month.

Budgets and forecasts are periods too: upload their trial balances in the same format and compare
them with actuals on the *Compare* page.

### 2. Upload the trial balances

Drop the files on the panels (Excel `.xlsx` or `.csv`):

* **Business-area TB** – GL rows × business-area columns (columns *G/L Code*, *G/L Description*,
  *FS Description*, *Total*, then one column per business area). Used for all Non-PRE amounts.
* **Cost-centre TB** – GL rows × cost-centre columns. Used for PRE (wages, salaries, welfare).
* **MIS figures** (optional) – columns *Particulars*, *MIS*, optional *Formula* and *Compare*, for the
  MIS check report.

The importer finds the header row itself, ignores blank and total rows and matches columns by header
name, so column order does not matter. The validation window lists:

* **new GLs, business areas or cost centres** not in the masters, with their amounts – add them on the
  *Masters* page (or import the masters workbook) and recalculate; nothing is dropped meanwhile, the
  amounts are flagged in the controls;
* duplicate columns or GLs, non-numeric cells, rows whose *Total* column disagrees with the sum of the
  columns, and ignored helper columns.

Uploading again replaces the active upload; earlier uploads stay in the history and can be downloaded.

*Derive monthly* creates a month's TB as the difference of two YTD periods.

### 3. Adjustments and inputs

*Adjustments & inputs* holds everything that is not in the TB:

* **TB adjustment rules** – regroup a GL, move a GL to another business area / cost centre, exclude a
  GL, add a fixed correction or reclassify an amount. Every rule needs a reason; it can apply to this
  period only or be *standing* (every period). Disable a rule instead of deleting it to keep the
  history.
* **Material-cost adjustments** – correct material cost per plant × product group to the worked-out
  figure (e.g. from an SAP material-cost report or production data), either as an amount or as a target MC % of sales.
* **Drivers** – quantities per plant × product group (machine hours, power units …) that allocation
  rules can use as a basis.
* **MIS figures** – the management-accounts figures to reconcile with.

### 4. Calculate and review the controls

*Calculations → Run calculation.* A run takes a second or two. Each run stores a snapshot of the
masters, rules and inputs it used, so it can be reproduced and audited later even if the masters
change.

Review the **controls** before submitting:

* *Business-area TB tie-out* – every rupee of the TB is used, excluded or flagged;
* *missing GLs / business areas / cost centres*, amounts on categories that the P&L does not use;
* *PRE per cost-centre TB vs business-area TB*;
* *Allocations: pool = allocated* – a pool with no basis is reported, not lost;
* *Manual allocation plugs*, *material-cost adjustments*;
* *Every category reaches PBT once*, *PBT per P&L vs profit per TB*.

Errors are not blocking – you may still submit with an explanation in the comment – but the approver
sees them.

### 5. Approve and publish

The analyst clicks **Submit for approval**. An approver (a different person) opens the run, checks the
controls and the reports and clicks **Approve**, then **Publish** – or **Reject** with a comment. A
published run is what the dashboard and reports show by default; publishing a new run of the same
period supersedes the earlier one.

When the month is final, an approver **locks the period** (*Periods & uploads*). Uploads, adjustments
and calculations are then blocked; only an admin can unlock.

## Reports

*Reports* shows the selected run (published by default). Tabs:

* **Plant-wise P&L** – one column per plant and the company total, with % of sales.
* **PG-wise P&L** – the product groups of one plant, with the plant total.
* **All PG** – product groups across all plants (company).
* **PG totals** – one product group in every plant.
* **MC %** – sales, material cost per TB, adjustment, worked-out material cost and MC % per plant × PG.
* **MIS check** – MIS figure, product-costing figure and difference; differences beyond the tolerance
  are highlighted.
* **Management summary** – the key lines by plant and by product group in presentation format.

**Drill-down:** click any amount. The panel shows the categories, allocations (Common / Corporate),
PRE items and material-cost adjustments that make up the value; open one to see the GL × business-area
or cost-centre rows behind it. The parts always add up to the amount.

**Export:** *Excel* exports the current report in the familiar workbook layout; *Full pack* exports
every report, the MC % and MIS sheets and the controls in one workbook; *PDF* prints the current
report on A3 landscape.

## Compare

* **P&L: run A vs run B** – any two runs: period on period, or actual vs budget. Choose company,
  plant or plant × product group level. Shows both values, the variance and the variance %.
* **MC % across periods** – material-cost % per plant × product group for the selected periods, with
  the change in percentage points (▲ red = higher material-cost share, ▼ green = lower).

## Dashboard

Key figures of the selected period with the variance to budget (when a budget period with the same
period code exists, e.g. actual `2026-09` and budget `2026-09`), the trend of sales, gross margin and PBT over the periods, sales and PBT by plant
and the profitability of each product group.

## Masters

The mappings and rules that drive the calculation: plants, product groups (and families), business
areas, GL accounts (category, Non-PRE / PRE / excluded), cost centres (business area, plant,
function), categories, PRE functions, allocation rules, P&L lines and report layouts.

* Every row has an **effective-from / to** date. To change a mapping from a date, use *New version…*:
  the rows valid on that date are closed the day before and copied; edit the copies. Periods ending
  before the date keep the old mapping.
* *Valid on* filters the list to one date.
* *Export to Excel* / *Import from Excel* exchange all masters as one workbook (merge or replace).
* The banner shows whether the configuration is valid (unknown lines in formulas, duplicate rules …).

The formula and rule syntax is described in [calculation-engine.md](calculation-engine.md).

## Audit trail

Every change – masters, uploads, adjustments, calculations, approvals, users, settings, logins – with
who, when and what changed (before → after). Filter by entity, action, user and date.
