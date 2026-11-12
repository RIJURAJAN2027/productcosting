"""End-to-end API tests on an in-memory SQLite database with the demo company."""
from __future__ import annotations

import io
import os

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

# SQLite in memory by default; set PC_TEST_DATABASE_URL to run the same tests on PostgreSQL
os.environ["PC_DATABASE_URL"] = os.environ.get("PC_TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ["PC_ENV"] = "test"

from app.db import Base, SessionLocal, get_engine, reset_engine  # noqa: E402
from app.main import create_app, init_db  # noqa: E402
from app.models import Tenant, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.demo import create_demo_company, seed_demo_runs  # noqa: E402
from app.settings import get_settings  # noqa: E402

PW = "Str0ngPassword!"


@pytest.fixture(scope="module")
def client():
    get_settings.cache_clear()
    reset_engine()
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    init_db()
    db = SessionLocal()
    tenant = db.query(Tenant).first()
    admin = db.query(User).filter_by(role="admin").first()
    admin.password_hash = hash_password(PW)
    for email, role in (("analyst@test.local", "analyst"), ("approver@test.local", "approver"),
                        ("viewer@test.local", "viewer")):
        db.add(User(tenant_id=tenant.id, email=email, name=role.title(), role=role, password_hash=hash_password(PW)))
    db.commit()
    company = create_demo_company(db, tenant, admin)
    db.commit()
    analyst = db.query(User).filter_by(email="analyst@test.local").first()
    seed_demo_runs(db, company, analyst, None)
    db.close()
    with TestClient(create_app()) as c:
        yield c


def login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def H(client):
    return {role: login(client, email) for role, email in (
        ("admin", get_settings().bootstrap_admin_email), ("analyst", "analyst@test.local"),
        ("approver", "approver@test.local"), ("viewer", "viewer@test.local"))}


@pytest.fixture(scope="module")
def company_id(client, H):
    return client.get("/api/companies", headers=H["viewer"]).json()[0]["id"]


def tb_xlsx(rows, dims, kind="BA"):
    wb = Workbook()
    ws = wb.active
    ws.title = f"Input_{kind} TB"
    ws["A1"] = "Trial balance export"
    ws.append([])
    ws.append(["G/L Code", "G/L Description", "FS Description", "Total"] + dims)
    for gl, desc, vals in rows:
        ws.append([gl, desc, "", sum(vals)] + vals)
    ws.append([None, "Grand total", None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------- auth & roles
def test_login_and_me(client, H):
    r = client.get("/api/auth/me", headers=H["analyst"])
    assert r.status_code == 200 and r.json()["role"] == "analyst"
    assert "edit" in r.json()["permissions"] and "approve" not in r.json()["permissions"]
    assert client.post("/api/auth/login", json={"email": "analyst@test.local", "password": "wrong"}).status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_viewer_cannot_edit(client, H, company_id):
    r = client.post(f"/api/companies/{company_id}/masters/plants", json={"code": "X"}, headers=H["viewer"])
    assert r.status_code == 403


def test_user_admin(client, H):
    r = client.post("/api/users", headers=H["admin"], json={"email": "new@test.local", "name": "New", "role": "viewer",
                                                             "password": "weak"})
    assert r.status_code == 422
    r = client.post("/api/users", headers=H["admin"], json={"email": "new@test.local", "name": "New", "role": "viewer",
                                                             "password": PW})
    assert r.status_code == 201
    assert client.get("/api/users", headers=H["analyst"]).status_code == 403


# ----------------------------------------------------------------------- dashboard & reports
def test_dashboard(client, H, company_id):
    d = client.get(f"/api/companies/{company_id}/dashboard", headers=H["viewer"]).json()
    assert len(d["trend"]) == 3 and d["current"]["company"]["sales"] > 0
    assert d["current"]["period"] == "2026-12" and d["current"]["budget"] is None   # no budget for December
    sep = next(p for p in d["periods"] if p["code"] == "2026-09")
    d = client.get(f"/api/companies/{company_id}/dashboard", params={"period_id": sep["id"]},
                   headers=H["viewer"]).json()
    assert d["current"]["budget"]["sales"] > 0


def _published_run(client, H, company_id):
    runs = client.get(f"/api/companies/{company_id}/runs", headers=H["viewer"]).json()
    return next(r for r in runs if r["status"] == "draft" and r["scenario"] == "actual")


def test_reports_json_excel_pdf(client, H, company_id):
    run = _published_run(client, H, company_id)
    rid = run["id"]
    rep = client.get(f"/api/runs/{rid}/reports/plant_wise", headers=H["viewer"]).json()
    pbt = next(r for r in rep["rows"] if r.get("line") == "pbt")
    assert len(pbt["values"]) == len(rep["columns"])
    company_total = pbt["values"][-1]
    assert company_total == pytest.approx(sum(pbt["values"][:-1]))
    pgw = client.get(f"/api/runs/{rid}/reports/pg_wise", params={"plant": "NTH", "unit": "lakh"},
                     headers=H["viewer"]).json()
    sales = next(r for r in pgw["rows"] if r.get("line") == "sales_total")
    assert sales["values"][-1] == pytest.approx(sum(sales["values"][:-2]))       # PGs + COM = plant total
    assert pgw["unit"] == "lakh"
    for name in ("all_pg", "pg_totals", "mc", "mis", "summary"):
        r = client.get(f"/api/runs/{rid}/reports/{name}", headers=H["viewer"])
        assert r.status_code == 200, (name, r.text)
    x = client.get(f"/api/runs/{rid}/export/pack.xlsx", headers=H["viewer"])
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert "Plant-wise PL" in wb.sheetnames and "PG-wise PL_NTH" in wb.sheetnames and "Controls" in wb.sheetnames
    p = client.get(f"/api/runs/{rid}/export/plant_wise.pdf", headers=H["viewer"])
    assert p.status_code == 200 and p.content[:4] == b"%PDF"


def test_drilldown_reconciles(client, H, company_id):
    rid = _published_run(client, H, company_id)["id"]
    d = client.get(f"/api/runs/{rid}/drilldown", params={"line": "pbt", "plant": "NTH", "code": "GATE"},
                   headers=H["viewer"]).json()
    assert d["explained"] == pytest.approx(d["value"], rel=1e-9, abs=1e-9)
    power = client.get(f"/api/runs/{rid}/drilldown", params={"line": "foh_power_and_fuel", "plant": "NTH",
                                                             "code": "GATE"}, headers=H["viewer"]).json()
    common = next(p for p in power["parts"] if p.get("component") == "common")
    assert common["sources"] and sum(s["value"] for s in common["sources"]) == pytest.approx(common["value"])
    # plant total and PRE parts reconcile too, and every part's source rows add up to the part
    tot = client.get(f"/api/runs/{rid}/drilldown", params={"line": "pbt", "plant": "NTH"}, headers=H["viewer"]).json()
    assert tot["explained"] == pytest.approx(tot["value"], rel=1e-9, abs=1e-9)
    pre = [p for p in tot["parts"] if p["kind"] == "P" and p["sources"]]
    assert pre, "expected PRE parts with cost-centre rows"
    for part in tot["parts"]:
        if part["sources"] and part["kind"] in ("C", "P") and len(part["sources"]) < 200:
            assert sum(s["value"] for s in part["sources"]) == pytest.approx(part["value"], rel=1e-6, abs=1e-9)


def test_compare_and_mc_comparison(client, H, company_id):
    runs = client.get(f"/api/companies/{company_id}/runs", headers=H["viewer"]).json()
    actual = next(r for r in runs if r["period_code"] == "2026-09" and r["scenario"] == "actual")
    budget = next(r for r in runs if r["period_code"] == "2026-09" and r["scenario"] == "budget")
    cmp = client.get("/api/compare", params={"run_a": actual["id"], "run_b": budget["id"]},
                     headers=H["viewer"]).json()
    assert any(r["line"] == "pbt" for r in cmp["rows"])
    pids = [p["id"] for p in client.get(f"/api/companies/{company_id}/periods", headers=H["viewer"]).json()
            if p["scenario"] == "actual"]
    mc = client.get(f"/api/companies/{company_id}/mc-comparison", params={"period_ids": pids},
                    headers=H["viewer"]).json()
    assert len(mc["periods"]) == 3


# ----------------------------------------------------------------------- inputs & workflow
def test_upload_validation_reports_new_codes(client, H, company_id):
    r = client.post(f"/api/companies/{company_id}/periods", headers=H["analyst"],
                    json={"code": "2027-01", "label": "Test period", "end_date": "2027-01-31",
                          "start_date": "2026-04-01"})
    assert r.status_code == 201
    pid = r.json()["id"]
    data = tb_xlsx([(50000, "Sales - Domestic", [-1000.0, -500.0, 0.0]),
                    (64000, "Power & Fuel", [100.0, 0.0, 50.0]),
                    (99999, "Brand new GL", [10.0, 0.0, 0.0])], ["NTH_GATE", "NTH_COM", "ZZZ_NEW"])
    r = client.post(f"/api/companies/{company_id}/periods/{pid}/uploads", headers=H["analyst"],
                    data={"kind": "BA_TB"}, files={"file": ("tb.xlsx", data)})
    assert r.status_code == 201, r.text
    up = r.json()
    assert up["status"] == "errors"
    types = {i["type"] for i in up["issues"]}
    assert {"new_gl", "new_ba"} <= types
    run = client.post(f"/api/companies/{company_id}/periods/{pid}/runs", headers=H["analyst"], json={})
    assert run.status_code == 201
    ctl = {c["code"]: c for c in run.json()["controls"]}
    assert ctl["np_unmapped_gl"]["level"] == "error" and ctl["np_unmapped_ba"]["level"] == "error"


def test_adjustment_requires_reason_and_is_audited(client, H, company_id):
    bad = client.post(f"/api/companies/{company_id}/adjustments", headers=H["analyst"],
                      json={"type": "exclude_gl", "definition": {"gls": ["65400"]}, "reason": " "})
    assert bad.status_code == 422
    ok = client.post(f"/api/companies/{company_id}/adjustments", headers=H["analyst"],
                     json={"type": "exclude_gl", "definition": {"gls": ["65400"]}, "reason": "Test exclusion"})
    assert ok.status_code == 201
    log = client.get("/api/audit", params={"company_id": company_id, "entity": "adjustment"},
                     headers=H["viewer"]).json()
    assert any(e["action"] == "create" for e in log)
    client.delete(f"/api/companies/{company_id}/adjustments/{ok.json()['id']}", headers=H["analyst"])


def test_maker_checker_and_period_lock(client, H, company_id):
    periods = client.get(f"/api/companies/{company_id}/periods", headers=H["viewer"]).json()
    p = next(x for x in periods if x["code"] == "2026-06")
    run = client.post(f"/api/companies/{company_id}/periods/{p['id']}/runs", headers=H["analyst"], json={}).json()
    rid = run["id"]
    assert client.post(f"/api/runs/{rid}/transition", json={"action": "approve"}, headers=H["approver"]).status_code \
        == 409   # must be submitted first
    assert client.post(f"/api/runs/{rid}/transition", json={"action": "submit", "comment": "ok"},
                       headers=H["analyst"]).json()["status"] == "submitted"
    assert client.post(f"/api/runs/{rid}/transition", json={"action": "approve"},
                       headers=H["analyst"]).status_code == 403          # analyst cannot approve
    assert client.post(f"/api/runs/{rid}/transition", json={"action": "approve"},
                       headers=H["approver"]).json()["status"] == "approved"
    assert client.post(f"/api/runs/{rid}/transition", json={"action": "publish"},
                       headers=H["approver"]).json()["status"] == "published"
    lock = client.post(f"/api/companies/{company_id}/periods/{p['id']}/lock", headers=H["approver"])
    assert lock.json()["status"] == "locked"
    assert client.post(f"/api/companies/{company_id}/periods/{p['id']}/runs", headers=H["analyst"],
                       json={}).status_code == 409
    # every input of a locked period is read-only
    base = f"/api/companies/{company_id}/periods/{p['id']}"
    assert client.post(f"{base}/mis", headers=H["analyst"], json={"label": "x", "expr": "[L:pbt]",
                                                                   "mis_value": 1}).status_code == 409
    assert client.post(f"{base}/mc-adjustments", headers=H["analyst"], json={
        "plant_code": "NTH", "pg_code": "GATE", "value": 1, "reason": "x"}).status_code == 409
    assert client.put(f"{base}/drivers", headers=H["analyst"], json={"driver": "d", "values": {}}).status_code == 409
    assert client.post(f"/api/companies/{company_id}/adjustments", headers=H["analyst"], json={
        "type": "exclude_gl", "definition": {"gls": ["1"]}, "reason": "x", "period_id": p["id"]}).status_code == 409
    client.post(f"/api/companies/{company_id}/periods/{p['id']}/unlock", headers=H["admin"])


def test_self_approval_blocked(client, H, company_id):
    periods = client.get(f"/api/companies/{company_id}/periods", headers=H["viewer"]).json()
    p = next(x for x in periods if x["code"] == "2026-12")
    rid = client.post(f"/api/companies/{company_id}/periods/{p['id']}/runs", headers=H["admin"], json={}).json()["id"]
    client.post(f"/api/runs/{rid}/transition", json={"action": "submit", "comment": "x"}, headers=H["admin"])
    r = client.post(f"/api/runs/{rid}/transition", json={"action": "approve"}, headers=H["admin"])
    assert r.status_code == 403 and "Maker-checker" in r.json()["detail"]


# ----------------------------------------------------------------------- masters
def test_masters_crud_export_import_validate(client, H, company_id):
    base = f"/api/companies/{company_id}/masters"
    lines = client.get(f"{base}/pnl-lines", headers=H["viewer"]).json()
    assert any(ln["code"] == "pbt" for ln in lines)
    bad = client.post(f"{base}/pnl-lines", headers=H["analyst"], json={"code": "x", "label": "X", "expr": "[L:a +"})
    assert bad.status_code == 422
    gl = client.post(f"{base}/gl-accounts", headers=H["analyst"],
                     json={"code": "99999", "description": "Brand new GL", "category": "Power & Fuel",
                           "gl_type": "NONPRE"})
    assert gl.status_code == 201
    upd = client.patch(f"{base}/gl-accounts/{gl.json()['id']}", headers=H["analyst"], json={"category": "Rent"})
    assert upd.json()["category"] == "Rent"
    x = client.get(f"/api/companies/{company_id}/masters-export", headers=H["viewer"])
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert {"GLAccounts", "AllocationRules", "PnLLines"} <= set(wb.sheetnames)
    r = client.post(f"/api/companies/{company_id}/masters-import", headers=H["analyst"], data={"mode": "merge"},
                    files={"file": ("m.xlsx", x.content)})
    assert r.status_code == 200 and r.json()["GLAccounts"]["updated"] > 0
    v = client.get(f"/api/companies/{company_id}/masters-validate", headers=H["viewer"]).json()
    assert not [p for p in v["problems"] if p["level"] == "error"]


def test_backup_and_restore(client, H, company_id):
    b = client.get(f"/api/companies/{company_id}/backup", headers=H["admin"])
    assert b.status_code == 200
    r = client.post("/api/restore", headers=H["admin"], data={"new_code": "DEMO2"},
                    files={"file": ("b.gz", b.content)})
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    runs = client.get(f"/api/companies/{new_id}/runs", headers=H["admin"]).json()
    assert runs and client.get(f"/api/runs/{runs[0]['id']}/reports/plant_wise",
                               headers=H["admin"]).status_code == 200
