"""Command-line administration.

    python -m app.cli init                     migrate the database to the latest schema and create the
                                               first organisation / admin user (and the demo company)
    python -m app.cli verify-workbook FILE     rebuild a legacy "Product costing" workbook with the engine
                                               and compare every value with the workbook (no database needed);
                                               --fix / --excel FILE also show the import corrections' effect
    python -m app.cli import-workbook FILE --company CODE [--period CODE ...]
                                               import a legacy workbook straight into the database
    python -m app.cli setup-windows            install on Windows Server without Docker (see install-windows-server.cmd)
    python -m app.cli backup --out DIR         pg_dump the database into DIR (keeps 30 days)
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


def cmd_init(_: argparse.Namespace) -> None:
    from .main import init_db
    from .migrate import migrate
    from .settings import get_settings

    s = get_settings()
    s.check_production()
    _wait_for_database()
    migrate()
    init_db()
    print("Database ready.")


def cmd_verify_workbook(args: argparse.Namespace) -> None:
    from .engine import EngineConfig
    from .importers.corrections import (CorrectionOptions, apply_corrections, calculate, correction_effect,
                                        reconciliation_workbook)
    from .importers.legacy_reference import compare_with_workbook
    from .importers.legacy_workbook import LegacyWorkbookImporter

    t0 = time.time()
    imp = LegacyWorkbookImporter(Path(args.file).read_bytes())
    res = imp.run()
    cfg = EngineConfig.from_dict(res.config)
    result = calculate(res)
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
    if args.fix or args.excel:
        changes = apply_corrections(res, CorrectionOptions())
        after = calculate(res)
        effect = correction_effect(result, after, (res.config.get("meta") or {}).get("pbt_line"))
        print(f"\nCorrections ({len(changes)}):")
        for c in changes:
            print(f"  {c['kind']:24s} {c['where'][:70]:70s} {c['amount']:+,.6f}  {c.get('source') or ''}")
        print("\nProfit before tax (before -> after corrections):")
        for r in effect["pbt"]:
            name = "Company" if r["plant"] == "__ALL__" else r["plant"]
            print(f"  {name:10s} {r['before']:14,.6f} -> {r['after']:14,.6f}  ({r['change']:+,.6f})")
        print("\nControls (before -> after):")
        for c in effect["controls"]:
            print(f"  {c['after_level'].upper():8s} {c['title']}: {c['before']:,.6f} -> {c['after']:,.6f}")
        print(f"\nReport values changed by the corrections: {effect['values_changed']} in "
              f"{len(effect['lines_changed'])} lines: {', '.join(effect['lines_changed'])}")
        if args.excel:
            Path(args.excel).write_bytes(reconciliation_workbook(changes, effect))
            print(f"Reconciliation written to {args.excel}")
    sys.exit(1 if len(cmp.mismatches) else 0)


def cmd_import_workbook(args: argparse.Namespace) -> None:
    """Import a legacy workbook into a company straight into the database – for workbooks larger
    than the hosting platform's upload limit (about 4.5 MB on Vercel)."""
    import datetime as dt

    from .db import SessionLocal
    from .importers.corrections import CorrectionOptions
    from .main import init_db
    from .migrate import migrate
    from .models import Company, Tenant
    from .services.config_builder import DEFAULT_SETTINGS
    from .services.legacy_import import import_legacy

    _wait_for_database()
    migrate()
    init_db()
    db = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).order_by(Tenant.id))
        company = db.scalar(select(Company).where(Company.tenant_id == tenant.id, Company.code == args.company))
        if company is None:
            company = Company(tenant_id=tenant.id, code=args.company, name=args.name or args.company,
                              settings=dict(DEFAULT_SETTINGS))
            db.add(company)
            db.flush()
        elif not args.replace:
            raise SystemExit(f"Company {args.company} already exists; add --replace to replace its masters.")
        fix = not args.no_fix
        out = import_legacy(db, company, Path(args.file).read_bytes(), Path(args.file).name, None,
                            args.period, args.label, dt.date.fromisoformat(args.end) if args.end else None,
                            dt.date.fromisoformat(args.start) if args.start else None,
                            corrections=CorrectionOptions(remove_plugs=fix, map_unused_categories=fix))
    finally:
        db.close()
    reg = out.get("regression") or {}
    print(f"Imported into company {args.company}: " + ", ".join(f"{v} {k.replace('_', ' ')}"
                                                                for k, v in out["counts"].items()))
    print(f"Import check: {reg.get('cells_compared', 0)} workbook values compared, "
          f"{reg.get('mismatches', 0)} differ by more than Rs 1.")
    corr = out.get("corrections") or {}
    for c in corr.get("changes") or []:
        print(f"  corrected: {c['kind'].replace('_', ' ')} – {c['where']} ({c['amount']:+,.6f})")
    for r in ((corr.get("effect") or {}).get("pbt") or []):
        if r["plant"] == "__ALL__":
            print(f"Company PBT {r['before']:,.6f} -> {r['after']:,.6f} ({r['change']:+,.6f}) after the corrections")
    if out.get("period_id"):
        print(f"Period {args.period} loaded with the workbook's trial balances; calculate it in the application.")


def cmd_setup_windows(args: argparse.Namespace) -> None:
    from .windows import setup_windows
    setup_windows(Path(args.root), args.port)


def cmd_backup(args: argparse.Namespace) -> None:
    from .windows import backup
    target = backup(Path(args.out), args.keep_days)
    print(f"Backup written to {target}")


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
    v.add_argument("--fix", action="store_true",
                   help="also apply the import corrections (remove plugs, map GLs whose category is not in the "
                        "P&L) and show their effect")
    v.add_argument("--excel", metavar="FILE", help="write a reconciliation workbook of the corrections (implies --fix)")
    v.set_defaults(func=cmd_verify_workbook)
    iw = sub.add_parser("import-workbook", help="import a legacy workbook into a company (no upload limit)")
    iw.add_argument("file")
    iw.add_argument("--company", required=True, help="company code (created if it does not exist)")
    iw.add_argument("--name", help="company name for a new company")
    iw.add_argument("--replace", action="store_true", help="replace the masters of an existing company")
    iw.add_argument("--period", help="period code, e.g. 2026-09 (loads the workbook's trial balances)")
    iw.add_argument("--label", help="period label, e.g. 'YTD Apr-Sep 2026'")
    iw.add_argument("--start", help="period start date YYYY-MM-DD")
    iw.add_argument("--end", help="period end date YYYY-MM-DD")
    iw.add_argument("--no-fix", action="store_true", help="import as-is, without the error corrections")
    iw.set_defaults(func=cmd_import_workbook)
    sw = sub.add_parser("setup-windows", help="install on Windows Server without Docker (run as administrator)")
    sw.add_argument("--root", default=str(Path(__file__).resolve().parents[2]), help="installation folder")
    sw.add_argument("--port", type=int, default=8080)
    sw.set_defaults(func=cmd_setup_windows)
    bk = sub.add_parser("backup", help="pg_dump the database into a folder (PostgreSQL client tools needed)")
    bk.add_argument("--out", default="backups")
    bk.add_argument("--keep-days", type=int, default=30)
    bk.set_defaults(func=cmd_backup)
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
