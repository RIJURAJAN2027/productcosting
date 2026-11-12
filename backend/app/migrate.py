"""Database schema migrations (Alembic), usable from the CLI and at application start."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from .db import Base, get_engine
from .settings import get_settings

MIGRATION_LOCK = 7300401          # PostgreSQL advisory-lock keys used by this application
BOOTSTRAP_LOCK = 7300402
ROOT = Path(__file__).resolve().parents[1]


def advisory_xact_lock(conn_or_session, key: int) -> None:
    """Serialise concurrent starts (several server processes or serverless instances). The lock is
    held until the current transaction ends, which also works behind a transaction-mode pooler."""
    if get_settings().is_sqlite:
        return
    conn_or_session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


def migrate() -> None:
    """Upgrade the schema to the latest revision (SQLite development databases are created directly)."""
    from alembic import command
    from alembic.config import Config

    if get_settings().is_sqlite:
        Base.metadata.create_all(get_engine())
        return
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["configure_logger"] = False
    with get_engine().begin() as conn:
        advisory_xact_lock(conn, MIGRATION_LOCK)
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
