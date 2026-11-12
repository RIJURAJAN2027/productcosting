"""Windows Server installation helpers (the parts that do not need Windows) and serving the web
interface from a folder given as a relative path."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.windows import backup_cmd, bootstrap_postgres, libpq_url, secret, server_cmd, task_xml

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_libpq_url_and_secrets():
    assert libpq_url("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert libpq_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    s = secret(40)
    assert len(s) == 40 and s.isalnum() and secret(40) != s


def test_scheduled_task_definitions_are_well_formed(tmp_path):
    boot = ET.fromstring(task_xml("Server", tmp_path / "run-server.cmd", tmp_path).encode("utf-16"))
    assert boot.find("t:Triggers/t:BootTrigger", NS) is not None
    assert boot.find("t:Settings/t:RestartOnFailure/t:Count", NS).text == "999"
    assert boot.find("t:Settings/t:ExecutionTimeLimit", NS).text == "PT0S"
    assert boot.find("t:Principals/t:Principal/t:UserId", NS).text == "S-1-5-18"        # SYSTEM
    assert boot.find("t:Actions/t:Exec/t:Command", NS).text.endswith("run-server.cmd")
    daily = ET.fromstring(task_xml("Backup & more <x>", tmp_path / "backup.cmd", tmp_path,
                                   daily_at="02:15").encode("utf-16"))
    assert daily.find("t:Triggers/t:CalendarTrigger/t:StartBoundary", NS).text.endswith("T02:15:00")
    assert daily.find("t:Settings/t:RestartOnFailure", NS) is None
    assert daily.find("t:RegistrationInfo/t:Description", NS).text == "Backup & more <x>"


def test_generated_batch_files_use_windows_line_endings():
    for text in (server_cmd(8123), backup_cmd()):
        assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
        text.encode("ascii")
    assert "--port 8123" in server_cmd(8123) and "uvicorn app.main:app" in server_cmd(8123)
    assert "app.cli backup" in backup_cmd()


def _admin_from_test_url():
    url = os.environ.get("PC_TEST_DATABASE_URL", "")
    m = re.match(r"postgresql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/", url)
    if not m:
        pytest.skip("set PC_TEST_DATABASE_URL to a PostgreSQL database to test the database set-up")
    return m.group(3), int(m.group(4) or 5432), m.group(1), m.group(2)


def test_bootstrap_postgres_creates_login_and_database_and_backup_works(tmp_path, monkeypatch):
    import psycopg

    host, port, admin, admin_pw = _admin_from_test_url()
    try:
        bootstrap_postgres(host, port, admin, admin_pw, db_name="pcwin_test", db_user="pcwin_test",
                           db_password="FirstPassw0rd")
        with psycopg.connect(host=host, port=port, user="pcwin_test", password="FirstPassw0rd",
                             dbname="pcwin_test") as conn:
            conn.execute("CREATE TABLE t (x int)")           # the login owns the database
            conn.execute("INSERT INTO t VALUES (42)")
        # running the set-up again keeps the database and sets the new password
        bootstrap_postgres(host, port, admin, admin_pw, db_name="pcwin_test", db_user="pcwin_test",
                           db_password="SecondPassw0rd")
        with psycopg.connect(host=host, port=port, user="pcwin_test", password="SecondPassw0rd",
                             dbname="pcwin_test") as conn:
            assert conn.execute("SELECT x FROM t").fetchone() == (42,)

        pg_dump = shutil.which("pg_dump")
        if pg_dump:
            client = int(re.search(r"(\d+)", subprocess.run([pg_dump, "--version"], capture_output=True,
                                                             text=True).stdout).group(1))
            with psycopg.connect(host=host, port=port, user=admin, password=admin_pw, dbname="postgres") as c:
                server = int(c.execute("SHOW server_version_num").fetchone()[0]) // 10000
            if client >= server:
                from app.settings import get_settings
                from app.windows import backup

                monkeypatch.setenv("PC_DATABASE_URL",
                                   f"postgresql+psycopg://pcwin_test:SecondPassw0rd@{host}:{port}/pcwin_test")
                get_settings.cache_clear()
                old = tmp_path / "costing_20000101_0000.dump"
                old.write_bytes(b"old")
                os.utime(old, (0, 0))
                target = backup(tmp_path, keep_days=30)
                assert target.exists() and target.read_bytes()[:5] == b"PGDMP"     # custom-format dump
                assert not old.exists()                                            # retention
                get_settings.cache_clear()
    finally:
        with psycopg.connect(host=host, port=port, user=admin, password=admin_pw, dbname="postgres",
                             autocommit=True) as conn:
            conn.execute("DROP DATABASE IF EXISTS pcwin_test")
            conn.execute("DROP ROLE IF EXISTS pcwin_test")


def test_web_interface_served_from_a_relative_folder(tmp_path, monkeypatch):
    from app.db import reset_engine
    from app.main import create_app
    from app.settings import get_settings

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log('ok')")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PC_FRONTEND_DIST", "dist")                 # relative, as people write it
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        assert client.get("/assets/app.js").text == "console.log('ok')"
        assert client.get("/reports").text == "<html>app</html>"           # client-side route
        assert client.get("/..%2f..%2fetc%2fpasswd").text == "<html>app</html>"
        assert client.head("/").status_code == 200
    finally:
        get_settings.cache_clear()
        reset_engine()
