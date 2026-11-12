# Administration guide

Installation, configuration, users, single sign-on, set-up from the legacy workbook, backups,
upgrades and troubleshooting.

## 1. Installation (Docker – on-premises or cloud)

Requirements: a Linux server or VM with Docker Engine 24+ and the Compose plugin, 2 vCPU, 4 GB RAM,
20 GB disk (more for many years of runs). Any cloud VM works the same way; the database can also be
a managed PostgreSQL 14+ (see *External database*).

### On a Windows PC or laptop

Good for trying the software and for a few users in one office. The application is available only
while the computer is on (and awake) with Docker Desktop running; colleagues on the same network use
`http://<computer's IP address>:8080`.

1. Install **Docker Desktop** from <https://www.docker.com/products/docker-desktop/> (accept the WSL 2
   option, restart if asked) and start it. Docker Desktop is free for personal use and for companies
   with fewer than 250 employees and less than USD 10 million annual revenue.
2. Download the software: on <https://github.com/RIJURAJAN2027/productcosting> click **Code → Download
   ZIP** and extract it, e.g. to `C:\ProductCosting`.
3. In that folder open `scripts`, right-click **install-windows.ps1 → Run with PowerShell**. It asks
   for the administrator's e-mail and the organisation name, creates `.env` with generated passwords,
   builds and starts everything (5–10 minutes the first time) and opens <http://localhost:8080>. The
   window shows the administrator password – keep it, and change it after signing in.
4. After restarting the computer: start Docker Desktop (the application starts with it), or run the
   script again. Docker Desktop → *Settings → General → Start Docker Desktop when you sign in* makes
   this automatic.

Notes for a laptop: turn on BitLocker (the database holds confidential figures), stop it from
sleeping while others use the application, and download a backup from *Administration → Backup &
restore* regularly. If colleagues cannot connect, allow *Docker Desktop Backend* for private networks
in Windows Defender Firewall. To update, download the new ZIP into the same folder (keep `.env`) and
run the script again.

### Oracle Cloud "Always Free" (Arm server)

Oracle's Always Free tier includes an Arm (Ampere A1) virtual machine – at the time of writing 2 CPUs
and 12 GB RAM – that runs this application with the same install script. Every Python and Docker
component used has Arm builds; the first build on the server takes about 10 minutes.

1. Sign up at <https://www.oracle.com/cloud/free/>. The **home region** cannot be changed later –
   choose one near your users. A credit card is needed for verification.
2. Recommended: *Billing & Cost Management → Upgrade and manage payment → Pay As You Go*. Always Free
   resources stay free, and Oracle does not reclaim idle Always Free servers of paying accounts (this
   application is idle most of the time). Add a small *Budget* with an e-mail alert as a safeguard.
3. *Compute → Instances → Create instance*: image **Canonical Ubuntu 24.04**, shape **Ampere →
   VM.Standard.A1.Flex** with 2 OCPUs and 12 GB memory (marked *Always Free-eligible*), a public
   subnet with a public IPv4 address, and *Generate a key pair for me* – download the private key.
   If Oracle reports *out of capacity*, try another availability domain or try again later.
4. Allow web traffic in Oracle's network: open the instance's subnet → its **security list** → *Add
   ingress rules* for source `0.0.0.0/0`, TCP, destination ports **80** and **443**.
5. Point your (sub)domain's **A record** to the instance's public IP.
6. Connect: `ssh -i <downloaded key> ubuntu@<public IP>` and run
   `sudo apt update && sudo apt install -y git`, then
   `sudo git clone https://github.com/RIJURAJAN2027/productcosting.git /opt/productcosting` and
   `cd /opt/productcosting && sudo scripts/install-server.sh` (answer *yes* to installing Docker).
   The script also opens ports 80 and 443 in the server's own firewall (Oracle's images block them).

### Windows Server (without Docker)

Docker Desktop does not run on Windows Server, so the application runs directly on Windows: Python
for the application, PostgreSQL for the database, a scheduled task that starts the web server with
Windows and restarts it if it stops, a nightly backup task and a Windows Firewall rule. Windows
Server 2016 or later, 2 CPUs, 4 GB RAM and 10 GB free disk are enough for a team.

1. **Python 3.13** – from <https://www.python.org/downloads/windows/> download the *Windows installer
   (64-bit)* of the latest 3.13 release, run it, choose **Customize installation** and on the second
   page tick **Install Python 3.13 for all users**, then *Install*.
2. **PostgreSQL** – from <https://www.postgresql.org/download/windows/> download the installer (by
   EDB), run it with the defaults, set a password for the database superuser **postgres** (keep it –
   the installer below asks for it once), keep port 5432, and untick *Stack Builder* at the end.
3. **The application** – take the Windows package **productcosting-windows.zip** (it contains the
   ready-built web interface; the plain source-code download does not) and extract it to
   `C:\ProductCosting`.
4. **Install** – in `C:\ProductCosting\scripts` right-click **install-windows-server.cmd → Run as
   administrator**. It installs the Python packages, asks for the administrator's e-mail, the
   organisation name and the PostgreSQL password, creates the application's own database login,
   writes `backend\.env` with generated secrets, prepares the database, registers the tasks
   *ProductCosting* (web server, at start-up) and *ProductCosting Backup* (02:15 every night into
   `C:\ProductCosting\backups`, kept 30 days), opens TCP 8080 in Windows Firewall for domain and
   private networks and starts the application. It prints the address and the administrator password.
5. Users open `http://<server name>:8080`. For HTTPS, publish it through IIS (URL Rewrite + ARR) or
   your company's reverse proxy with your certificate (see *HTTPS* below).

Logs are in `C:\ProductCosting\logs`. To **upgrade**, extract the new package over the folder (keep
`backend\.env`) and run the installer again; it updates the packages, migrates the database and
restarts the application. The tasks can be checked in *Task Scheduler*. Without the package, the web
interface can be built with Node.js 20+: `cd frontend`, `npm ci`, `npm run build`.

### Quickest: the install script (Hostinger VPS or any Ubuntu server)

`scripts/install-server.sh` asks three questions, writes `.env` with generated passwords and secrets,
starts PostgreSQL and the application and – with a domain – an HTTPS proxy (Caddy) that obtains and
renews a free Let's Encrypt certificate. It warns when the domain does not point to the server yet,
can turn on automatic operating-system security updates and schedule a daily database backup, and
removes old images after upgrades.

**On Hostinger** (*VPS* plans – the *Web*, *Cloud* and *WordPress* hosting plans run PHP / Node.js
sites with MySQL and cannot run this application):

1. **Buy / open the VPS** in hPanel: *KVM 2* (2 vCPU, 8 GB RAM) is comfortable, *KVM 1* (1 vCPU,
   4 GB) is enough for a few users. Choose the data-centre location closest to your users.
2. **Operating system**: on the VPS dashboard open *OS & Panel → Operating System* and pick the
   **Docker** application template (Ubuntu with Docker installed), or plain *Ubuntu 24.04 LTS* – the
   script installs Docker if it is missing. Changing the operating system erases the server, so do it
   before installing anything. Set a strong root password or add your SSH key.
3. **Domain**: under *Domains*, select your domain, open *DNS / Nameservers* and add an **A record**,
   e.g. name `costing`, pointing to the VPS's IP address (shown on the VPS overview). Wait until
   `costing.yourdomain.com` resolves to that IP (`dig A +short costing.yourdomain.com`; usually minutes,
   at most a day).
4. **Firewall**: if you use the VPS firewall (*Security → Firewall*), add inbound rules for TCP 22,
   80 and 443 (and UDP 443).
5. **Install**: connect with SSH – `ssh root@<VPS IP>` (the SSH details are on the VPS overview) – and
   run:

   ```bash
   git clone https://github.com/RIJURAJAN2027/productcosting.git /opt/productcosting
   cd /opt/productcosting
   scripts/install-server.sh
   ```

   Answer the questions (domain `costing.yourdomain.com`, administrator e-mail, organisation name).
   The first build takes a few minutes. The script prints the address and the generated administrator
   password – sign in and change it under *Profile*.

6. **Upgrade** later with `cd /opt/productcosting && git pull && scripts/install-server.sh` (the
   existing `.env` and data are kept). Hostinger's VPS snapshots and weekly backups are a useful extra
   layer on top of the daily database backup (section 6).

Without a domain the script serves plain http on port 8080 – fine for a trial, but use a domain and
HTTPS before entering real figures or passwords.

### Manual set-up

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

**Behind a TLS-inspecting corporate proxy**, give the build the proxy's CA certificate: paste it
into `docker/extra-ca.pem` (or run `BUILD_CA_FILE=/path/to/ca.pem scripts/install-server.sh`). It is
used only to download packages and is not stored in the image. With plain Docker:
`docker build --secret id=ca,src=/path/to/corporate-ca.pem -t productcosting:latest .`

### HTTPS

**Built in:** set `DOMAIN=costing.example.com`, `COMPOSE_PROFILES=https` and `APP_BIND=127.0.0.1` in
`.env` and run `docker compose up -d`. The `caddy` service (see `docker/Caddyfile`) listens on ports 80
and 443, obtains and renews a Let's Encrypt certificate for the domain, redirects http to https, adds
security headers and forwards to the application, whose own port is then reachable only from the
server itself. The domain's DNS must point to the server and ports 80 and 443 must be open.

**Your own proxy:** put the application behind your reverse proxy or load balancer (nginx, IIS ARR,
Traefik, an Azure / AWS / GCP load balancer) that terminates TLS and forwards to port 8080. Example
nginx server block:

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

### Vercel (serverless)

The repository contains a `vercel.json` that deploys the web interface and the API as two Vercel
Services of one project (the API answers `/api`, `/docs` and `/redoc`; everything else is the web
interface). Vercel has no permanent disk, so the data lives in a hosted PostgreSQL database.

1. **Import the repository** in Vercel (*Add New → Project*), keep the root directory `./` – Vercel
   reads `vercel.json` and shows the two services.
2. **Environment variables** (Production and Preview):

   | Variable | Value |
   |---|---|
   | `PC_SECRET_KEY` | a random string of 32+ characters (e.g. run `crypto.randomUUID() + crypto.randomUUID()` in a browser console) |
   | `PC_BOOTSTRAP_ADMIN_EMAIL` | the first administrator's e-mail |
   | `PC_BOOTSTRAP_ADMIN_PASSWORD` | their first password (10+ characters, upper and lower case, a digit) |
   | `PC_BOOTSTRAP_TENANT` | your organisation's name |
   | `PC_LOAD_DEMO_COMPANY` | `true` to add the fictional demo company, `false` for none |

3. **Deploy**, then add the database: *Storage → Create Database → Neon (Postgres)*, choose a region
   close to your users, and connect it to the project with the default variable names (this sets
   `DATABASE_URL`; with a custom prefix, set `PC_DATABASE_URL` to the connection string yourself).
   **Redeploy** so the functions see it.
4. Open the deployment URL and sign in. On the first request the application creates the database
   tables and the first administrator itself (no start-up script is needed); concurrent first
   requests are serialised with database locks.

Notes:

* On Vercel the application runs in production mode unless `PC_ENV` says otherwise, and it refuses
  to start without a database, a secret key or with the default admin password (the error is in the
  function logs).
* Limits per request: about **4.5 MB** of upload (trial balances are normally far smaller; import a
  larger legacy workbook with the command line, see section 5), 300 seconds and 2 GB of memory on
  the Hobby plan – a legacy-workbook import takes about half a minute and 0.6 GB.
* Vercel's Hobby plan is for personal, non-commercial use; use a Pro plan for a company's
  production data, and keep previews pointed at a separate database if they should not see it.
* Single sign-on works across serverless instances (its state is a signed token); register
  `https://<your-domain>/api/auth/sso/callback` as the redirect URI.

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
| `DATABASE_URL` / `POSTGRES_URL` | – | used when `PC_DATABASE_URL` is not set (hosted PostgreSQL such as Neon) |
| `PC_AUTO_MIGRATE` | on for Vercel | migrate the schema and create the first admin on the first request |
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
(as adjustment rules with a reason), the worked-out material cost and the MIS figures.

**Import check.** The imported configuration is calculated on the workbook's own trial balances and
**every value is compared with the workbook** (tolerance ₹1) – before any correction – so the import
is proven to reproduce the workbook.

**Corrections of workbook errors** (both on by default; untick them to import the workbook as it is):

* *Plugs* – fixed amounts typed into allocation formulas (e.g. `= pool × share − 0.5`) and amounts
  added to report figures are removed: every pool is then shared out by its basis only and the
  reports show calculated values.
* *GLs whose category never reaches the P&L* – a GL whose category has no row in the workbook's
  reallocation sheet is silently left out of the workbook's P&L. Such a GL is mapped to the category
  that the other GLs with the same FS description use (largest amount first); the GL master keeps a
  note of the old and new category. A GL with no such peers stays flagged by the controls.

The result lists every correction (where, workbook cell, amount), profit before tax by plant before
and after, the controls before and after, and every report value that changed. Review it before
calculating; any correction can be changed afterwards in *Masters*.

The same check runs without the database, and `--excel` writes a reconciliation workbook (summary,
corrections, changed values) for sign-off of the corrected baseline:

```bash
cd backend
python -m app.cli verify-workbook ~/workbook.xlsx                  # import check only
python -m app.cli verify-workbook ~/workbook.xlsx --fix --excel reconciliation.xlsx
# with Docker
docker compose cp ~/workbook.xlsx app:/tmp/workbook.xlsx
docker compose exec app python -m app.cli verify-workbook /tmp/workbook.xlsx --fix
docker compose exec app rm /tmp/workbook.xlsx
```

**Large workbooks.** Uploads through the browser are limited by the server (about 4.5 MB on Vercel).
A larger workbook is imported from any computer with Python, straight into the database:

```bash
cd backend && pip install -r requirements.txt
PC_DATABASE_URL='postgresql://user:password@host/db?sslmode=require' \
  python -m app.cli import-workbook ~/workbook.xlsx --company MAIN --name "My Company" \
  --period 2026-09 --label "YTD Apr-Sep 2026" --start 2026-04-01 --end 2026-09-30
```

Add `--replace` to re-import into an existing company and `--no-fix` to skip the corrections.

After the import, maintain the masters in the application (or with the masters Excel export /
import) – the workbook is no longer needed.

## 6. Backup and restore

**Database (recommended, everything):**

```bash
scripts/backup-db.sh                      # writes backups/costing_YYYYmmdd_HHMM.dump, keeps 30 days
scripts/restore-db.sh backups/costing_20261112_0200.dump
```

Schedule the backup daily (the install script offers to add the cron job; `KEEP_DAYS` changes the
retention) and copy the files off the server. The scripts use `pg_dump` / `pg_restore` inside the
`db` container.

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
