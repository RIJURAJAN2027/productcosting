"""FastAPI application entry point."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select

from . import __version__
from .api import admin, auth, masters, periods, runs
from .db import Base, get_engine, session_scope
from .models import Tenant, User
from .security import hash_password
from .settings import get_settings

log = logging.getLogger("productcosting")

DESCRIPTION = """
**Product Costing & Profitability** turns trial balances (business-area and cost-centre TBs) into
plant-wise and product-group-wise P&L reports.

* **Masters** – business areas, GL accounts, cost centres, product groups, categories, PRE functions,
  allocation rules and the P&L line structure; versioned by effective date, editable and Excel import/export.
* **Inputs** – TB uploads with validation, adjustment rules, material-cost adjustments, drivers, MIS figures.
* **Engine** – tagging → adjustments → aggregation → Common / Corporate / PRE allocation → P&L → controls.
* **Workflow** – maker-checker approval (draft → submitted → approved → published), period locking, audit trail.
* **Reports** – PG-wise / plant-wise / PG totals / all PG P&L, MC %, MIS check, management summary,
  comparisons, drill-down to GL and business area / cost centre; Excel and PDF export.

Authenticate with `POST /api/auth/login` and send `Authorization: Bearer <token>`.
"""


def create_app() -> FastAPI:
    s = get_settings()
    s.check_production()
    app = FastAPI(title="Product Costing & Profitability API", version=__version__, description=DESCRIPTION,
                  openapi_tags=[{"name": t} for t in ("auth", "administration", "masters", "periods & inputs",
                                                       "runs & reports")])
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_list, allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["Content-Disposition"])
    for r in (auth.router, admin.router, masters.router, periods.router, runs.router):
        app.include_router(r)

    @app.get("/api/health", tags=["auth"])
    def health():
        with session_scope() as db:
            db.execute(select(func.count(Tenant.id)))
        return {"status": "ok", "version": __version__}

    @app.exception_handler(ValueError)
    async def value_error(_, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    dist = Path(s.frontend_dist) if s.frontend_dist else None
    if dist and dist.exists():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = dist / path
            if path and target.is_file() and dist in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    @app.on_event("startup")
    def startup() -> None:
        init_db()
    return app


def init_db() -> None:
    """Create tables for SQLite / first start, then bootstrap the first tenant and admin."""
    s = get_settings()
    engine = get_engine()
    if s.is_sqlite or s.env in ("development", "test"):
        Base.metadata.create_all(engine)
    with session_scope() as db:
        if db.scalar(select(func.count(Tenant.id))):
            return
        tenant = Tenant(name=s.bootstrap_tenant, slug="default", license_plan="standard",
                        license_max_companies=10, license_max_users=50)
        db.add(tenant)
        db.flush()
        admin = User(tenant_id=tenant.id, email=s.bootstrap_admin_email.lower(), name="Administrator", role="admin",
                     password_hash=hash_password(s.bootstrap_admin_password), is_superadmin=True)
        db.add(admin)
        db.flush()
        log.warning("Created first tenant and admin user %s – change the password after first login.",
                    s.bootstrap_admin_email)
        if s.load_demo_company and s.env != "test":
            from .services.demo import create_demo_company, seed_demo_runs
            analyst = approver = None
            if s.env != "production":
                # sample users with a published password – never created on a production server
                demo_pw = hash_password("Demo@12345!")
                analyst = User(tenant_id=tenant.id, email="analyst@demo.local", name="Demo Analyst",
                               role="analyst", password_hash=demo_pw)
                approver = User(tenant_id=tenant.id, email="approver@demo.local", name="Demo Approver",
                                role="approver", password_hash=demo_pw)
                viewer = User(tenant_id=tenant.id, email="viewer@demo.local", name="Demo Viewer", role="viewer",
                              password_hash=demo_pw)
                db.add_all([analyst, approver, viewer])
                db.flush()
            company = create_demo_company(db, tenant, admin)
            db.commit()
            seed_demo_runs(db, company, analyst or admin, approver)


app = create_app()
