# Architecture

```
 Browser (React + TypeScript SPA)
        │  HTTPS, JSON, bearer token
        ▼
 FastAPI application  ── app/api/*        REST endpoints, OpenAPI at /docs
        │               app/services/*   uploads, runs, reports, exports, backups, legacy import
        │               app/engine/*     calculation engine (pure Python / NumPy / pandas)
        │               app/importers/*  TB reader, legacy workbook importer + regression comparison
        ▼
 PostgreSQL (SQLAlchemy 2, Alembic migrations)
```

One container image serves the API and the built single-page application; a second container runs
PostgreSQL (or use a managed database). Several Uvicorn worker processes handle requests; there is
no other moving part (no queue or cache server) – a full calculation takes well under a second.

The same code also runs serverless on Vercel (`vercel.json`): the web interface is a static service,
the API a Python function, and the database a hosted PostgreSQL (e.g. Neon). In that mode connections
are not pooled in the function (the provider's pooler does it), the schema is migrated and the first
administrator created on the first request under PostgreSQL advisory locks, and single-sign-on state
is a signed token, so any instance can complete a login.

## Backend modules

| Module | Responsibility |
|---|---|
| `app/engine/config.py` | `EngineConfig`: plants, product groups, BA / GL / CC maps, categories, PRE functions, allocation rules, P&L lines; validation |
| `app/engine/expr.py` | safe formula parser / evaluator for P&L lines |
| `app/engine/core.py` | `CostingEngine`: adjust → tag → aggregate → allocate → P&L → controls; `EngineResult` with values, long table, drill-down |
| `app/importers/tb.py` | TB reader: header detection, name-based columns, validation issues, comparison with masters |
| `app/importers/legacy_workbook.py` | translates the legacy Excel workbook's formulas into an engine configuration |
| `app/importers/legacy_reference.py` | compares an engine result with every value of the workbook |
| `app/services/config_builder.py` | builds the engine configuration from the masters valid on a date |
| `app/services/runs.py` | executes runs (with config / input snapshots), stores values, approval workflow |
| `app/services/reports.py` | report building, MC %, MIS check, comparisons, dashboard, drill-down |
| `app/services/exports.py` | Excel (workbook layout) and PDF exports |
| `app/services/masters_io.py`, `backup.py`, `demo.py` | masters Excel import / export, company backup / restore, demo company |
| `app/api/*` | auth (password + OIDC SSO), admin, masters, periods & inputs, runs & reports |
| `app/cli.py` | `init` (migrate + bootstrap), `verify-workbook`, `set-password`, `create-admin` |

## Data model

```
tenant ─┬─ user (role, company access, SSO identity)
        └─ company (settings) ─┬─ masters, each with effective_from / effective_to:
                               │    plant, product_group, business_area, gl_account, cost_centre,
                               │    category, pre_function, allocation_rule, pnl_line
                               ├─ report_template (row layout of each report)
                               ├─ period (actual / budget / forecast, YTD or month, open / locked)
                               │    ├─ upload (BA_TB, CC_TB, MIS; summary + validation issues)
                               │    │    └─ tb_line (gl, dimension, amount)   long format
                               │    ├─ adjustment_rule (period-specific; NULL period = standing)
                               │    ├─ mc_adjustment, driver_value, mis_line
                               │    └─ calc_run (status, config + input snapshot, controls, timings)
                               │         └─ run_value (line, plant, code, value)
                               └─ audit_log (who, when, what, before → after)
```

Trial balances are stored in long format (one row per GL × business area / cost centre with a
non-zero amount), so a TB with 1,000 GLs × 900 columns is a few tens of thousands of rows.

## Calculation runs

1. The masters valid on the period's end date are assembled into an `EngineConfig`; the active
   uploads, adjustment rules, MC adjustments and drivers into `EngineInputs`.
2. The engine calculates; every P&L line for every plant × product group, plant total, product-group
   total and the company total is stored in `run_value`, in rupees.
3. The run keeps a JSON snapshot of the configuration and the inputs, so it can be recalculated and
   drilled into later even after the masters change. Drill-down re-runs the engine from the snapshot
   (cached per process).
4. Workflow: `draft → submitted → approved → published` (or `rejected`); publishing supersedes the
   earlier published run of the period. Locked periods refuse uploads, input changes and runs.

## Security

* Password sign-in (bcrypt hashes, lockout after repeated failures) and OpenID Connect SSO; JWT bearer
  tokens signed with `PC_SECRET_KEY`.
* Role-based permissions (`view`, `edit`, `approve`, `admin`) checked on every endpoint; users can be
  limited to companies; tenants are isolated on every query.
* Maker-checker on runs; audit log for every change.
* The production configuration check refuses default secrets.

## Front end

Vite + React 18 + TypeScript, TanStack Query for data, Recharts for charts, a hash router (works
behind any reverse proxy path without server rewrites). Pages: dashboard, periods & uploads,
adjustments & inputs, calculations, reports (with drill-down and exports), compare, masters, audit
trail, administration, profile. Light / dark themes from CSS variables; layouts work down to phone
width.

## Tests

* `tests/test_engine.py` – engine steps, allocation features, overrides, controls, drill-down,
  performance (1,000 GLs × 900 columns).
* `tests/test_api.py` – end-to-end API with the demo company: uploads, validation, masters, runs,
  maker-checker, reports, exports, drill-down, comparisons, backup / restore (SQLite, or PostgreSQL
  with `PC_TEST_DATABASE_URL`).
* `tests/test_regression_workbook.py` – with `PC_LEGACY_WORKBOOK=/path/workbook.xlsx`: imports the
  legacy workbook and requires every compared value to match within ₹1, directly and after a round
  trip through the API and the database. Skipped when no workbook is given (workbooks are confidential
  and never committed).
