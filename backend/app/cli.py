"""Command-line administration.

    python -m app.cli init                     migrate the database to the latest schema and create the
                                               first organisation / admin user (and the demo company)
    python -m app.cli verify-workbook FILE     rebuild a legacy "Product costing" workbook with the engine
                                               and compare every value with the workbook (no database needed)
    python -m app.cli set-password EMAIL       set a user's password (reads it from PC_NEW_PASSWORD or asks)
    python -m app.cli create-admin EMAIL       create an extra administrator in the first organisation
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

from sqlalchemy import func, select, text


def _wait_for_database(timeout_s: int = 90) -> None:
    from .db import get_engine
    deadline = time.time() + timeout_s
    while True:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # pragma: no cover - depends on the environment
            if time.time() > deadline:
                raise SystemExit(f"Database not reachable: {exc}")
            print("Waiting for the database …", file=sys.stderr)
            time.sleep(2)


def migrate() -> None:
    """Upgrade the schema with Alembic (SQLite development databases are created directly)."""
    from alembic import command
    from alembic.config import Config

    from .db import Base, get_engine
    from .settings import get_settings

    s = get_settings()
    if s.is_sqlite:
        Base.metadata.create_all(get_engine())
        return
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.attributes["configure_logger"] = False
    with get_engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def cmd_init(_: argparse.Namespace) -> None:
    from .main import init_db
    from .settings import get_settings

    s = get_settings()
    s.check_production()
    _wait_for_database()
    migrate()
    init_db()
    print("Database ready.")


def cmd_verify_workbook(args: argparse.Namespace) -> None:
    from .engine import CostingEngine, EngineConfig, EngineInputs
    from .importers.legacy_reference import compare_with_workbook
    from .importers.legacy_workbook import LegacyWorkbookImporter

    t0 = time.time()
    imp = LegacyWorkbookImporter(Path(args.file).read_bytes())
    res = imp.run()
    cfg = EngineConfig.from_dict(res.config)
    result = CostingEngine(cfg, EngineInputs(res.ba_tb, res.cc_tb, res.adjustments, {}, res.mc_adjustments)).run()
    cmp = compare_with_workbook(imp, result, tolerance_rupees=args.tolerance)
    summary = cmp.summary()
    print(f"Imported: {len(cfg.plants)} plants, {len(cfg.product_groups)} product groups, "
          f"{len(cfg.business_areas)} business areas, {len(cfg.gl_accounts)} GLs, {len(cfg.cost_centres)} cost "
          f"centres, {len(cfg.rules)} allocation rules, {len(cfg.lines)} P&L lines")
    print(f"Calculated in {result.timings.get('total', 0):.2f}s (total {time.time() - t0:.1f}s incl. reading)")
    print(f"\nComparison with the workbook (tolerance Rs {args.tolerance}):")
    print(summary.to_string(index=False))
    print(f"\nCells compared: {len(cmp.table)}, mismatches: {len(cmp.mismatches)}")
    if len(cmp.mismatches):
        cols = [c for c in ("section", "sheet", "cell", "label", "expected", "actual", "abs_diff")
                if c in cmp.mismatches.columns]
        print(cmp.mismatches.sort_values("abs_diff", ascending=False)[cols].head(args.show).to_string(index=False))
    print("\nControls:")
    for c in result.controls:
        print(f"  {c.level.upper():8s} {c.title} ({c.amount:,.6f})")
    counts = res.note_counts()
    if counts:
        print("\nImporter notes:", ", ".join(f"{v} {k}" for k, v in counts.items()))
    sys.exit(1 if len(cmp.mismatches) else 0)


def cmd_set_password(args: argparse.Namespace) -> None:
    from .db import session_scope
    from .models import User
    from .security import hash_password, password_problems

    pw = os.environ.get("PC_NEW_PASSWORD") or getpass.getpass("New password: ")
    problems = password_problems(pw)
    if problems:
        raise SystemExit("Password needs " + ", ".join(problems))
    with session_scope() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == args.email.lower()))
        if user is None:
            raise SystemExit(f"No user {args.email}")
        user.password_hash, user.failed_logins, user.is_active = hash_password(pw), 0, True
    print(f"Password set for {args.email}; the account is unlocked and active.")


def cmd_create_admin(args: argparse.Namespace) -> None:
    from .db import session_scope
    from .models import Tenant, User
    from .security import hash_password, password_problems

    pw = os.environ.get("PC_NEW_PASSWORD") or getpass.getpass("Password: ")
    problems = password_problems(pw)
    if problems:
        raise SystemExit("Password needs " + ", ".join(problems))
    with session_scope() as db:
        tenant = db.scalar(select(Tenant).order_by(Tenant.id))
        if tenant is None:
            raise SystemExit("Run `python -m app.cli init` first.")
        if db.scalar(select(User).where(func.lower(User.email) == args.email.lower())):
            raise SystemExit(f"User {args.email} already exists; use set-password.")
        db.add(User(tenant_id=tenant.id, email=args.email.lower(), name=args.name or "Administrator", role="admin",
                    password_hash=hash_password(pw)))
    print(f"Administrator {args.email} created.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Product Costing administration")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="migrate the database and create the first admin").set_defaults(func=cmd_init)
    v = sub.add_parser("verify-workbook", help="compare the engine with a legacy workbook")
    v.add_argument("file")
    v.add_argument("--tolerance", type=float, default=1.0, help="tolerance in rupees (default 1)")
    v.add_argument("--show", type=int, default=25, help="mismatches to list")
    v.set_defaults(func=cmd_verify_workbook)
    sp = sub.add_parser("set-password", help="set a user's password")
    sp.add_argument("email")
    sp.set_defaults(func=cmd_set_password)
    ca = sub.add_parser("create-admin", help="create an administrator")
    ca.add_argument("email")
    ca.add_argument("--name", default="")
    ca.set_defaults(func=cmd_create_admin)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
