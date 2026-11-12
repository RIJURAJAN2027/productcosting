"""Installation on Windows Server without Docker.

`python -m app.cli setup-windows` (called by scripts/install-windows-server.cmd) creates the
PostgreSQL database and its login, writes backend/.env with generated secrets, migrates the
database, registers a start-up task that runs the web server under the SYSTEM account, a nightly
backup task, opens the port in Windows Firewall and starts the application.
"""
from __future__ import annotations

import datetime as dt
import getpass
import glob
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

TASK_SERVER = "ProductCosting"
TASK_BACKUP = "ProductCosting Backup"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def secret(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def libpq_url(url: str) -> str:
    """postgresql+psycopg://… (SQLAlchemy) -> postgresql://… (libpq tools)."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def bootstrap_postgres(host: str, port: int, admin_user: str, admin_password: str, db_name: str = "costing",
                       db_user: str = "costing", db_password: str | None = None) -> None:
    """Create (or update the password of) the application's login and create its database."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(host=host, port=port, user=admin_user, password=admin_password, dbname="postgres",
                         autocommit=True) as conn:
        verb = "ALTER" if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (db_user,)).fetchone() \
            else "CREATE"
        conn.execute(sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(db_user),
                                                                           sql.Literal(db_password)))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db_name),
                                                                       sql.Identifier(db_user)))


def task_xml(description: str, command: Path, workdir: Path, daily_at: str | None = None) -> str:
    """Task Scheduler definition: at start-up with restart on failure, or daily at HH:MM; runs as SYSTEM."""
    if daily_at:
        trigger = (f"<CalendarTrigger><StartBoundary>2000-01-01T{daily_at}:00</StartBoundary>"
                   "<Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
                   "</CalendarTrigger>")
        restart = ""
    else:
        trigger = "<BootTrigger><Enabled>true</Enabled></BootTrigger>"
        restart = "\n    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(description)}</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>{restart}
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(str(command))}</Command><WorkingDirectory>{escape(str(workdir))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def server_cmd(port: int) -> str:
    return (
        "@echo off\r\n"
        "rem Starts the Product Costing web server (run by the \"ProductCosting\" scheduled task).\r\n"
        "set PYTHONUTF8=1\r\n"
        "if not exist \"%~dp0logs\" mkdir \"%~dp0logs\"\r\n"
        "if exist \"%~dp0logs\\server.log\" for %%F in (\"%~dp0logs\\server.log\") do "
        "if %%~zF GTR 10485760 move /y \"%%~fF\" \"%~dp0logs\\server.previous.log\" >nul\r\n"
        "cd /d \"%~dp0backend\"\r\n"
        f"\"%~dp0venv\\Scripts\\python.exe\" -m uvicorn app.main:app --host 0.0.0.0 --port {port} "
        "--proxy-headers >> \"%~dp0logs\\server.log\" 2>&1\r\n"
    )


def backup_cmd() -> str:
    return (
        "@echo off\r\n"
        "rem Nightly database backup (run by the \"ProductCosting Backup\" scheduled task).\r\n"
        "set PYTHONUTF8=1\r\n"
        "if not exist \"%~dp0logs\" mkdir \"%~dp0logs\"\r\n"
        "cd /d \"%~dp0backend\"\r\n"
        "\"%~dp0venv\\Scripts\\python.exe\" -m app.cli backup --out \"%~dp0backups\" >> "
        "\"%~dp0logs\\backup.log\" 2>&1\r\n"
    )


def find_pg_dump() -> str | None:
    found = shutil.which("pg_dump")
    if found:
        return found
    candidates = glob.glob(r"C:\Program Files\PostgreSQL\*\bin\pg_dump.exe")

    def version(path: str) -> float:
        m = re.search(r"PostgreSQL[\\/](\d+(?:\.\d+)?)", path)
        return float(m.group(1)) if m else 0.0

    return max(candidates, key=version) if candidates else None


def backup(out_dir: Path, keep_days: int = 30) -> Path:
    """pg_dump of the application database into out_dir; dumps older than keep_days are removed."""
    from sqlalchemy.engine import make_url

    from .settings import get_settings

    pg_dump = find_pg_dump()
    if not pg_dump:
        raise SystemExit("pg_dump was not found – install the PostgreSQL command line tools.")
    url = make_url(get_settings().database_url)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"costing_{dt.datetime.now():%Y%m%d_%H%M}.dump"
    env = dict(os.environ, PGPASSWORD=url.password or "")
    subprocess.run([pg_dump, "--format=custom", "--compress=6", "--file", str(target),
                    "--host", url.host or "localhost", "--port", str(url.port or 5432),
                    "--username", url.username or "costing", url.database or "costing"], check=True, env=env)
    cutoff = time.time() - keep_days * 86400
    for old in out_dir.glob("costing_*.dump"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
    return target


# ---------------------------------------------------------------------------------- Windows only
def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def _register_task(name: str, xml: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "task.xml"
        path.write_text(xml, encoding="utf-16")
        result = _run(["schtasks", "/Create", "/TN", name, "/XML", str(path), "/F"], check=False)
        if result.returncode != 0:
            raise SystemExit(f"Could not create the scheduled task {name!r}: {result.stdout} {result.stderr}")


def _stop_server() -> None:
    _run(["schtasks", "/End", "/TN", TASK_SERVER], check=False)
    # the venv's python.exe starts the real interpreter as a child process, so match the command line
    _run(["powershell", "-NoProfile", "-Command",
          "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn app.main:app*' -and "
          f"$_.ProcessId -ne {os.getpid()} }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
         check=False)


def _wait_until_healthy(port: int, seconds: int = 180) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(2)
    return False


def setup_windows(root: Path, port: int = 8080) -> None:
    if os.name != "nt":
        raise SystemExit("setup-windows is for Windows; on Linux use scripts/install-server.sh.")
    root = root.resolve()
    backend = root / "backend"
    env_file = backend / ".env"
    dist = root / "frontend" / "dist"
    if not (dist / "index.html").exists():
        raise SystemExit("The web interface (frontend\\dist) is missing. Use the Windows package "
                         "productcosting-windows.zip (not the source-code download), or build it with Node.js: "
                         "cd frontend, npm ci, npm run build.")
    new_password = email = None
    if env_file.exists():
        print("Using the existing configuration (backend\\.env).")
    else:
        print("\nFirst-time set-up")
        email = input("Administrator e-mail: ").strip() or "admin@example.com"
        org = input("Organisation name: ").strip().replace('"', "").replace("$", "") or "My Organisation"
        demo = input("Add the fictional demo company for training? [y/N] ").strip().lower().startswith("y")
        print("\nThe application gets its own database in PostgreSQL. Enter the password of the PostgreSQL")
        print("superuser 'postgres' that you chose when installing PostgreSQL (it is used once, not stored).")
        pg_host = os.environ.get("PC_PG_HOST", "localhost")
        pg_port = int(os.environ.get("PC_PG_PORT", "5432"))
        db_password = secret(32)
        for attempt in range(3):
            try:
                bootstrap_postgres(pg_host, pg_port, "postgres", getpass.getpass("PostgreSQL 'postgres' password: "),
                                   db_password=db_password)
                break
            except Exception as exc:  # wrong password, server not running …
                print(f"Could not connect to PostgreSQL: {exc}".strip())
                if attempt == 2:
                    raise SystemExit("Check that PostgreSQL is installed and running, and the password.")
        new_password = secret(8) + "Aa7" + secret(6)
        env_file.write_text("\n".join([
            f"# Created by the Windows installer on {dt.date.today()}. Keep this file private.",
            "PC_ENV=production",
            f"PC_DATABASE_URL=postgresql+psycopg://costing:{db_password}@{pg_host}:{pg_port}/costing",
            f"PC_SECRET_KEY={secret(64)}",
            f'PC_BOOTSTRAP_TENANT="{org}"',
            f"PC_BOOTSTRAP_ADMIN_EMAIL={email}",
            f"PC_BOOTSTRAP_ADMIN_PASSWORD={new_password}",
            f"PC_LOAD_DEMO_COMPANY={'true' if demo else 'false'}",
            f"PC_FRONTEND_DIST={dist}",
        ]) + "\n", encoding="utf-8")

    os.chdir(backend)                          # settings read backend\.env
    from .db import reset_engine
    from .main import init_db
    from .migrate import migrate
    from .settings import get_settings
    get_settings.cache_clear()
    reset_engine()
    print("\nPreparing the database…")
    migrate()
    init_db()

    (root / "run-server.cmd").write_bytes(server_cmd(port).encode("ascii"))
    (root / "backup.cmd").write_bytes(backup_cmd().encode("ascii"))
    print("Registering the start-up and backup tasks…")
    _stop_server()
    _register_task(TASK_SERVER, task_xml("Product Costing web server", root / "run-server.cmd", root))
    if find_pg_dump():
        _register_task(TASK_BACKUP, task_xml("Product Costing nightly database backup", root / "backup.cmd", root,
                                             daily_at="02:15"))
    else:
        print("pg_dump was not found, so no nightly backup was scheduled.")
    rule = f"Product Costing (TCP {port})"
    _run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"], check=False)
    _run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={rule}", "dir=in", "action=allow",
          "protocol=TCP", f"localport={port}", "profile=domain,private"], check=False)
    _run(["schtasks", "/Run", "/TN", TASK_SERVER], check=False)
    print("Starting the application…")
    if not _wait_until_healthy(port):
        raise SystemExit(f"The application did not start. See {root / 'logs' / 'server.log'}.")
    host = socket.gethostname()
    print(f"\nProduct Costing is running: http://{host}:{port}   (on this server: http://localhost:{port})")
    if new_password:
        print(f"Sign in as {email} with the password:  {new_password}")
        print("Change it after the first sign-in (Profile). It is also stored in backend\\.env.")
    print(f"Backups: {root / 'backups'} every night at 02:15 (kept 30 days). Logs: {root / 'logs'}.")
    print("Upgrade: extract the new package over this folder and run install-windows-server.cmd again.")
