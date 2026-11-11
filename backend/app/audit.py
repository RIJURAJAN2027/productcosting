"""Audit trail helper: every upload, master change, adjustment and approval is logged."""
from __future__ import annotations

import datetime as dt
import decimal
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog, User


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def row_dict(obj: Any, exclude: tuple[str, ...] = ("created_at", "updated_at")) -> dict:
    return {c.name: _clean(getattr(obj, c.name)) for c in obj.__table__.columns if c.name not in exclude}


def log(db: Session, user: User | None, action: str, entity: str, entity_id: Any = None,
        company_id: int | None = None, **details: Any) -> AuditLog:
    entry = AuditLog(tenant_id=user.tenant_id if user else None, company_id=company_id,
                     user_id=user.id if user else None, user_email=user.email if user else "system",
                     action=action, entity=entity, entity_id=None if entity_id is None else str(entity_id),
                     details=_clean(details))
    db.add(entry)
    return entry


def diff(before: dict, after: dict) -> dict:
    return {k: {"from": before.get(k), "to": after.get(k)} for k in after if before.get(k) != after.get(k)}
