# Product costing – open questions

Answer under each question. "Today:" is what the software does now, so "confirm" is a valid answer.

## A. Trial balance inputs

**A1. What period does a TB cover?** Is every TB you upload year-to-date (Apr → month end), or monthly? Is the fiscal year always April–March?
Today: you choose YTD or month per period; a month can be derived as YTD(this month) − YTD(last month).

**A2. Signs.** In the BA TB, Sales 50000 is −2,926,980,362 (credit), but in the CC TB the same GL is +2,499,484. Are the CC TB signs reversed, or is the CC TB a different extract?
Today: both are read as debit + / credit −.

**A3. Which TB is the source for what?** Non-PRE amounts come from the BA TB and PRE (people cost) from the CC TB. Is that right? Should the non-PRE amounts in the CC TB be ignored completely?
Today: yes, and the PRE totals of the two TBs are cross-checked.

**A4. Helper columns.** `7COR` in the BA TB repeats other columns (it doubles GL 52232 and 60060). Are there other subtotal or helper columns in either TB, and do their names change from month to month?
Today: columns listed in the company's "ignore columns" setting are skipped.

**A5. Row Total column.** Is "Total" always the sum of the business-area / cost-centre columns, or can it legitimately differ?
Today: a difference is a warning, never an error.

**A6. Zero and blank columns.** The CC TB has 718 cost-centre columns but only 169 have amounts. Are the empty ones closed cost centres or simply idle this period?
Today: zero amounts are ignored.

**A7. Same SAP extract every month?** Do header names stay fixed (`G/L Code`, `G/L Description`, `FS Description`, `Total`)? Can header rows or title rows move?
Today: the header row is detected automatically and columns are matched by name.

**A8. Re-uploads.** If a corrected TB is uploaded for a month that is already calculated or approved, what should happen?
Today: the new upload replaces the active one, older uploads stay in history, and a new calculation run is needed.

**A9. Rounding.** Should reports be rounded to crore / lakh only at the end, with totals as the sum of the unrounded values?
Today: no intermediate rounding.

## B. Business areas, plants, product groups

**B1. BA naming.** Is the first character of a BA code always the plant (1 = MPK, 3 = …, 7 = …)? Can one plant have two digits?
Today: the master's Plant column is used, not the code.

**B2. The 8 plants.** The master names MPK, MMN, KPM, CBR, SPR, JAM, KSA and USA. Are KSA and USA (overseas) part of the plant-wise P&L, or reported separately / excluded? Is there a "company total" that excludes some plants?

**B3. Plant order and grouping.** In what order should plants appear in reports? Are there plant groups (for example domestic vs overseas) with subtotals?

**B4. Product groups.** The BA list uses 20 codes (API, BAL, DNA, FIS, GGC, Traded, LSV, …). Is "Traded" a product group with its own P&L, or a separate line of business? What do ACT, SRV, OFE and FIS stand for, and are they all reported?

**B5. Families.** Product family (Ball, Common, Corp, GGC, Others, Special projects, Traded, …): is it used only for grouping/subtotals in reports, or does it drive any allocation? Should "Common" and "Corp" really be families, given they are COM/COR?

**B6. COM vs COR.** Confirm: COM = plant-common cost, shared over that plant's product groups; COR = corporate cost, shared over all plants and product groups.

**B7. Special BAs.** `1COR_UA`, `1DOM_BA`, `1EXP_BA`, `1MCR_BA` and `1PRS_BA` are all COR. Are Domestic and Export sales BAs really corporate, or should their sales go to product groups?

**B8. Sales on COM/COR BAs.** If a sales or material GL is booked on a COM or COR business area, should it be allocated like a cost, or is that a booking error to report?

**B9. A plant that makes only some product groups.** When COM is spread, should a plant's COM go only to product groups that have sales in that plant that period?
Today: it goes by the rule's basis, so groups with zero basis get nothing.

## C. GL accounts and categories

**C1. Where does the P&L category of each GL come from?** Your Masters sheet's GL block is the "MC grouping" (49 GLs). The other GLs had their category in the Non-PRE and PRE sheets. Should the software keep **two separate fields**, the P&L category and the MC grouping? Today they are one field (Category1), which is risky.

**C2. Full category list.** Please send the full list of P&L categories, each with its sign (income / cost) and the P&L line it feeds.

**C3. PRE categories.** Which categories are PRE: only Wages, Salaries and Others? "Others" is also an MC grouping used by Cash Discount GLs 64255 and 64280. Should the PRE category be renamed (e.g. "PRE Others") to remove the clash?

**C4. Excluded GLs.** Which GLs are deliberately excluded (balance-sheet items, inter-company, provisions, inventory changes)? Should "excluded" mean the amount appears nowhere in the P&L, or only below PBT?

**C5. Inventory change GLs** (52002–52039, "Bill of materials"). Are they part of material cost (consumption = purchases ± inventory change)? Do they go to product groups by BA like purchases?

**C6. GL in both PRE and Non-PRE.** Can one GL be PRE for some cost centres and Non-PRE for others (for example contract labour)?
Today: one GL has one type.

**C7. A new GL with no category** in a month's TB: should the calculation stop (error), continue with the amount flagged as unmapped (today), or use a default "Other expenses" category?

**C8. Duplicate descriptions.** GLs 64255 and 64280 are both "Cash Discount / Payment Difference". Is that expected (different company codes)?

## D. Cost centres and PRE

**D1. BArea on a cost centre.** BArea is a 4-character prefix (`1PLG`). Is the plant always the BA's plant with the same prefix?
Today: the plant is derived that way.

**D2. Function vs Allocation logic.** Confirm: Function is descriptive (Plug, Corporate, Plant HR …) and Allocation logic decides where the cost goes (a product group, COM, COR or a PRE function such as QC or R&D).

**D3. The 13 PRE functions.** For each of Domestic, Export, R&D, QC, Stores, PLED, CMG, QA, SCM Corp, SCM, CPD, PED and Nil:
- (a) factory or corporate group?
- (b) shared within its own plant, pooled across plants, or a custom rule?
- (c) what basis (sales, material cost, direct wages, headcount, hours …)?

**D4. "Nil" allocation.** Cost centre with allocation "Nil": should its cost be excluded, kept unallocated at plant level, or treated as an error?

**D5. Cost centre with no BArea** (981010). Which plant does it belong to? Should a missing BArea block the calculation?

**D6. Direct PRE.** A cost centre whose allocation logic is a product group (e.g. GGC): is 100% of its wages charged to that product group in its own plant only?

**D7. Shared cost centres across plants.** Do any cost centres serve more than one plant (for example corporate HR)? How should those be split?

**D8. PRE cross-check tolerance.** How large a difference between PRE per the CC TB and per the BA TB is acceptable?

## E. Allocation rules

**E1. Basis per pool.** For COM and COR of each category, what basis is used: sales, material cost, direct cost, direct PRE, machine hours or fixed percentages? Is it the same for every category or different per category (for example power by machine hours)?

**E2. Basis period.** Should the basis be YTD figures when the period is YTD, and the month's figures for a month?

**E3. Negative or zero basis.** If a product group has negative sales (returns) or zero basis in a plant, what should happen?
Today: that share becomes 0 or negative and the control reports it.

**E4. Corporate scope.** Is COR spread over all plants including KSA/USA, or only Indian plants? Over sales of the company or of each plant?

**E5. Fixed percentages.** Do any allocations use fixed percentages agreed by management (for example R&D 40% to GGC)? Who changes them and how often?

**E6. Hard-coded plugs.** Your workbook had a few hard-coded balancing figures. Should the new software keep them, or must every rupee be allocated by a rule (plugs not allowed)?

**E7. Allocation order.** Is a pool ever allocated using a basis that already includes another allocated pool (e.g. COR on "cost after COM")? Please confirm the order.

**E8. Drivers.** Are there non-financial drivers (headcount, machine hours, kWh, tonnage) you upload monthly? In what format?

## F. Material cost and MC %

**F1. Definition of material cost.** Which categories make up material cost (Bill of materials, Freight inward, Subcontracting, PPV, Scrap, Indirect material, PI Adjustment, Revaluation …)?

**F2. MC grouping use.** Is the MC grouping used only for the "MC %" report, or does it also change the P&L?

**F3. Material cost adjustments.** When do you override material cost (actual MC %, standard cost, a target MC %)? Is the difference posted to a specific P&L line?

**F4. Scrap sales and PI adjustments.** Do they reduce material cost or sit in other income?

## G. Adjustments

**G1. Recurring adjustments.** Which corrections happen every month (regroup GL, move BA, exclude, fixed amounts)? Should they repeat automatically every period until switched off?

**G2. Approval.** Do adjustments need approval separately from the calculation run?

**G3. Limits.** Should large adjustments (above an amount) be flagged?

## H. P&L output

**H1. P&L lines.** Please send the exact list of P&L lines in order (Sales, Material cost, Contribution, Gross margin, Plant profit, PBT …) with the formula for each.

**H2. Gross margin vs plant profit vs PBT.** Which costs are deducted at each level (direct PRE, factory PRE, COM, COR, finance cost, depreciation)?

**H3. Break-even.** Confirm the formula: BEP = fixed cost ÷ contribution %, and per month = BEP ÷ months in the period. Which costs are fixed?

**H4. Reports needed.** Which reports are required each month: plant-wise, PG-wise, PG totals, all PG, MC %, MIS check, management summary, budget vs actual? Any new ones (e.g. family-wise, customer-wise)?

**H5. Number display.** Should the reports be in ₹ crore with 2 decimals, and percentages to 1 decimal?

## I. Controls and reconciliation

**I1. MIS check.** Which MIS figures must match (sales, PBT, material cost …), and what difference is allowed?
Today: the tolerance is ₹1.

**I2. Blocking errors.** Which problems must stop submission (unmapped GL, BA or CC; allocation not balanced; PBT ≠ TB profit) and which are warnings only?

**I3. Profit tie-out.** Confirm: company PBT in the P&L must equal profit per the TB after excluding the excluded GLs.

## J. Periods, budget, comparisons

**J1. Monthly cycle.** Is a run done every month for YTD, and is the month itself also reported?

**J2. Budget.** Is the budget uploaded as a TB in the same format, with the same masters and rules? Is there a revised forecast during the year?

**J3. Prior year.** Do you need last year's same period for comparison? Should it be recalculated with today's masters, or kept as it was reported?

**J4. Locking.** After approval and publishing, should a period be locked so that master changes can never affect it?

## K. Future changes (the most important part)

**K1. Effective date of a master change.** When a cost centre moves to a different allocation in October, should:
- (a) October YTD (Apr–Oct) be recalculated entirely with the new mapping, or
- (b) Apr–Sep stay on the old mapping and only October onward use the new one?

Today: the mapping valid on the period end date is used for the whole YTD, which means (a). Option (b) needs monthly calculation that is added up into YTD.

**K2. Restating history.** If a mapping error is found later, should past published periods be restated (new version, old version kept) or left as published?

**K3. New product group or plant mid-year.** How should it appear in YTD reports: from the month it starts, with zeros before?

**K4. A product group is discontinued.** Should it disappear from reports, or stay with zero / old values for the rest of the year?

**K5. Merging or splitting product groups** (e.g. GGC split into two). Do comparisons with earlier periods need a mapping old → new?

**K6. A business area or cost centre is closed.** Should its leftover balances still be accepted? Should a closed code that suddenly has amounts be an error?

**K7. Renaming codes.** When SAP renames a cost centre (new code, same department), should history be linked to the new code?

**K8. Rule changes.** If an allocation basis changes from April next year, must the old rule stay available for re-running old periods?
Today: rules are versioned by date like masters.

**K9. Who may change masters and rules?** Should master changes need a second person's approval (maker-checker), like calculation runs?

**K10. Upload add-ons.** When the master Excel is uploaded and a code already exists with a different value, should the upload:
- (a) update it (today),
- (b) refuse it and show it, or
- (c) ask per row?

**K11. Removing rows.** Should removing a master row ever be allowed, or should rows only be closed with an end date?
Today: closed from the chosen date; deleted only with "All periods".

**K12. Auto-adding new codes.** When a TB contains a new GL, BA or CC, should the software:
- (a) only list it (today),
- (b) add it automatically with a guessed mapping (e.g. CC from its BArea prefix, GL by its FS Description), or
- (c) block the calculation until it is mapped?

**K13. Mapping guesses.** Can a new cost centre's BArea and allocation be guessed from similar codes (same first digits)? Can a new GL's category be guessed from its FS Description? Are these guesses acceptable if they are clearly marked?

**K14. Settings changes.** If the PRE category list, the COM/COR codes or the calculation unit change, do they apply only to future periods?

**K15. Multiple companies.** Will other L&T companies or divisions use the software with their own masters? Can they share GL categories?

**K16. Audit needs.** How long must uploads, runs and change history be kept? Do auditors need an export of the change log?

## L. Users and process

**L1. Roles.** Who uploads TBs, who maintains masters, who approves runs, who only views?

**L2. Single sign-on.** Should users sign in with company accounts (Azure AD / SSO) instead of passwords?

**L3. Deadlines.** By which working day each month must the reports be published? Is there a monthly checklist to follow?

## M. Things I noticed in your files

1. BA TB rows for GL 52232 and 60060: `7COR` is a subtotal column. Confirm it should always be ignored.
2. BA TB total is −140.03 crore, CC TB total is +92.09 crore. What should each total tie to?
3. CC TB contains sales and forex GLs (50000, 50003, 50010, 50011). Why are sales in the CC TB, and should they be ignored?
4. Masters: 186 BAs, but only 39 have amounts in this TB. Are the rest inactive this period?
5. 717 cost centres in the master, 169 with amounts, and every TB cost centre is in the master.
6. 313 GLs in the TBs have no category in the Masters sheet. They need the Non-PRE / PRE sheets, which is why I need the full workbook.
7. Allocation logic "Traded" exists as both a product group and a cost-centre allocation. Confirm they mean the same thing.
8. Cost centre 981010 has no BArea.
