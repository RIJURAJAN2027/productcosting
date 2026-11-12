"""FastAPI application entry point."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select

from . import __version__
from .api import admin, auth, masters, periods, runs
from .db import Base, get_engine, session_scope
from .migrate import BOOTSTRAP_LOCK, advisory_xact_lock, migrate
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

    dist = Path(s.frontend_dist).resolve() if s.frontend_dist else None
    if dist and dist.exists():
        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def spa(path: str):
            target = dist / path
            if path and target.is_file() and dist in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    app.state.initialized = False
    init_lock = threading.Lock()

    def ensure_initialized() -> None:
        if app.state.initialized:
            return
        with init_lock:
            if not app.state.initialized:
                init_db()
                app.state.initialized = True

    @app.on_event("startup")
    def startup() -> None:
        ensure_initialized()

    @app.middleware("http")
    async def initialize_on_first_request(request, call_next):
        # serverless platforms may not run start-up events: prepare the database on first use
        if not app.state.initialized:
            await run_in_threadpool(ensure_initialized)
        return await call_next(request)
    return app


def init_db() -> None:
    """Prepare the database: create the tables (SQLite / development) or run the migrations
    (``auto_migrate``, e.g. on Vercel), then create the first tenant and admin when it is empty."""
    s = get_settings()
    engine = get_engine()
    if s.auto_migrate:
        migrate()
    elif s.is_sqlite or s.env in ("development", "test"):
        Base.metadata.create_all(engine)
    with session_scope() as db:
        advisory_xact_lock(db, BOOTSTRAP_LOCK)       # only one process creates the first admin
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
