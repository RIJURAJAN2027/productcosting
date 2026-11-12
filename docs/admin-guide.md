# Administration guide

Installation, configuration, users, single sign-on, set-up from the legacy workbook, backups,
upgrades and troubleshooting.

## 1. Installation (Docker – on-premises or cloud)

Requirements: a Linux server or VM with Docker Engine 24+ and the Compose plugin, 2 vCPU, 4 GB RAM,
20 GB disk (more for many years of runs). Any cloud VM works the same way; the database can also be
a managed PostgreSQL 14+ (see *External database*).

```bash
git clone https://github.com/RIJURAJAN2027/productcosting.git
cd productcosting
cp .env.example .env
```

Edit `.env` and set at least:

| Variable | Meaning |
|---|---|
| `POSTGRES_PASSWORD` | database password (letters and digits) |
| `PC_SECRET_KEY` | random string of 32+ characters that signs login tokens – `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `PC_BOOTSTRAP_ADMIN_EMAIL` / `PC_BOOTSTRAP_ADMIN_PASSWORD` | the first administrator, created when the database is empty |
| `PC_BOOTSTRAP_TENANT` | your organisation's name |
| `APP_PORT` | port on the host (default 8080) |

```bash
docker compose up -d --build
docker compose logs -f app        # "Database ready." then "Application startup complete."
```

Open `http://<server>:8080`, sign in as the bootstrap administrator and change the password
(*Profile*). The server refuses to start in production with the default secret key or the default
admin password.

**Behind a TLS-inspecting corporate proxy**, give the build the proxy's CA certificate (it is used
only to download packages and is not stored in the image):

```bash
docker build --secret id=ca,src=/path/to/corporate-ca.pem -t productcosting:latest .
docker compose up -d
```

### HTTPS

Put the application behind your reverse proxy or load balancer (nginx, IIS ARR, Traefik, an Azure /
AWS / GCP load balancer) that terminates TLS and forwards to port 8080. Example nginx server block:

```nginx
server {
    listen 443 ssl;
    server_name costing.example.com;
    ssl_certificate     /etc/ssl/costing.crt;
    ssl_certificate_key /etc/ssl/costing.key;
    client_max_body_size 60m;            # TB and workbook uploads
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

### External database

Remove the `db` service from `docker-compose.yml` (or run the image alone) and set
`PC_DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname`. The schema is created and
upgraded automatically on start (Alembic migrations).

### Without Docker

Python 3.12+ and Node 20+:

```bash
cd frontend && npm ci && npm run build && cd ..
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export PC_ENV=production PC_DATABASE_URL=... PC_SECRET_KEY=... PC_BOOTSTRAP_ADMIN_PASSWORD=...
export PC_FRONTEND_DIST=$(pwd)/../frontend/dist
python -m app.cli init
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers
```

On Windows Server, run the same commands in PowerShell and register uvicorn as a service (e.g. with
NSSM), or use Docker Desktop / WSL2.

## 2. Configuration reference

All settings are environment variables prefixed `PC_` (see `backend/app/settings.py`).

| Variable | Default | |
|---|---|---|
| `PC_ENV` | `production` in the image | `development` creates tables directly and adds demo users |
| `PC_DATABASE_URL` | – | SQLAlchemy URL (`postgresql+psycopg://…`) |
| `PC_SECRET_KEY` | – | token signing key; changing it signs everybody out |
| `PC_ACCESS_TOKEN_MINUTES` | 720 | login lifetime |
| `PC_MAX_UPLOAD_MB` | 50 | largest upload |
| `PC_CORS_ORIGINS` | empty | only when the UI is served from another origin |
| `PC_LOAD_DEMO_COMPANY` | true | create the fictional demo company on first start |
| `PC_OIDC_*` | empty | single sign-on, below |
| `WEB_CONCURRENCY` | 2 | server processes (≈ number of CPU cores) |

Company settings are edited in the application (*Administration → Company & settings*): display unit,
number format (Indian 1,00,000 or international 100,000), decimals, MIS tolerance, fiscal-year start,
TB columns to ignore on import, and which P&L lines are sales, material cost and PBT (`meta`).

## 3. Users and roles

*Administration → Users.* Roles: **admin**, **analyst** (costing analyst), **approver**, **viewer** –
see the [user manual](user-manual.md#roles). A user can be limited to some companies. After 8 failed
sign-ins an account is locked; unlock it in the user list.

Maker-checker is on by default: the submitter of a run cannot approve or publish it.
*Organisation & licence → Approval policy* can switch this off for one-person teams (not recommended).

Forgotten admin password (on the server):

```bash
docker compose exec -e PC_NEW_PASSWORD='N3w!Password' app python -m app.cli set-password admin@example.com
```

`cli create-admin <email>` adds another administrator the same way.

## 4. Single sign-on (OpenID Connect)

Works with Microsoft Entra ID (Azure AD), Okta, Google Workspace, Keycloak, Auth0 and any OIDC
provider. Example for Entra ID:

1. *App registrations → New registration*, redirect URI (Web):
   `https://costing.example.com/api/auth/sso/callback`.
2. *Certificates & secrets → New client secret*.
3. Set in `.env` and restart (`docker compose up -d`):

```
PC_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
PC_OIDC_CLIENT_ID=<application (client) id>
PC_OIDC_CLIENT_SECRET=<secret>
PC_OIDC_REDIRECT_URI=https://costing.example.com/api/auth/sso/callback
```

The sign-in page then shows *Sign in with your organisation*. Users must exist in the application
first (*Users → New user*, sign-in “SSO”); the e-mail address in the token is matched to the user and
the role comes from the application. Password sign-in keeps working for local users.

## 5. Companies and the legacy workbook

Each company has its own masters, periods and runs. To set up a company from the existing
*Product costing – plant wise* Excel workbook:

1. *Administration → Company & settings → New company*, then select it in the top bar.
2. *Administration → Import legacy workbook*, enter a period code / label / dates and drop the
   workbook.

The importer reads the workbook's formulas and builds the plants, product groups, business areas, GL
accounts, cost centres, categories, PRE functions, allocation rules, P&L lines and the report
layouts. With a period it also loads the two trial balances, manual edits in the calculation sheets
(as adjustment rules with a reason), the worked-out material cost and the MIS figures, calculates and
**compares every value with the workbook** (tolerance ₹1). The result lists the cells compared per
section and any differences, plus importer notes (formulas it simplified, plugs, cells it could not
translate).

The same check runs without the database:

```bash
docker compose cp ~/workbook.xlsx app:/tmp/workbook.xlsx
docker compose exec app python -m app.cli verify-workbook /tmp/workbook.xlsx
docker compose exec app rm /tmp/workbook.xlsx
# or, from a source checkout
cd backend && python -m app.cli verify-workbook ~/workbook.xlsx
```

After the import, maintain the masters in the application (or with the masters Excel export /
import) – the workbook is no longer needed.

## 6. Backup and restore

**Database (recommended, everything):**

```bash
scripts/backup-db.sh                      # writes backups/costing_YYYYmmdd_HHMM.dump
scripts/restore-db.sh backups/costing_20261112_0200.dump
```

Schedule the backup daily (cron / Task Scheduler) and copy the files off the server. The scripts
use `pg_dump` / `pg_restore` inside the `db` container.

**One company (from the application):** *Administration → Backup & restore* downloads a compressed
file with the company's settings, masters, periods, uploads, adjustments, MIS figures and runs, and
restores such a file as a new company – useful to copy a company to a test server.

## 7. Upgrades

```bash
git pull
docker compose up -d --build
```

Database migrations run automatically on start. Take a database backup first.

## 8. Monitoring and logs

* Health check: `GET /api/health` (used by the container's `HEALTHCHECK`).
* Logs: `docker compose logs app` – requests, migrations, errors.
* API documentation (OpenAPI): `/docs` and `/redoc` on the server.

## 9. Licence and organisations

The first organisation (tenant) and its super-admin are created at the first start. The licence
limits (users, companies, expiry) are shown in *Organisation & licence*; only the super-admin (the
vendor account) can change them. For a hosted service, keep the super-admin account with the
operator and give each customer organisation normal admin accounts. The demo company does not count
towards the company limit.

## 10. Security notes

* Passwords are stored as bcrypt hashes; sign-in tokens are signed with `PC_SECRET_KEY`.
* Every change is written to the audit trail; entries cannot be edited from the application.
* Customer trial balances and workbooks are confidential: never commit them to a source repository
  (`.gitignore` excludes Excel and CSV files) and restrict access to the backup files.
* Keep the server patched and the application behind HTTPS.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `Unsafe production configuration` on start | set `PC_SECRET_KEY` (32+ characters) and a non-default `PC_BOOTSTRAP_ADMIN_PASSWORD` |
| `Waiting for the database …` | the database is not reachable: check `PC_DATABASE_URL` and that `db` is healthy |
| Upload says *new GLs / business areas* | add them to the masters (or import the masters workbook) and recalculate |
| A control shows *allocations: pool ≠ allocated* | a rule's basis is zero or invalid for a plant – open the run's controls for the rule and plant |
| *Maker-checker* message | another approver has to approve and publish the run |
| SSO: *No active user for this SSO identity* | create the user with the same e-mail first |
