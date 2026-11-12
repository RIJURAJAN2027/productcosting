"""Password hashing (bcrypt) and JWT access tokens."""
from __future__ import annotations

import datetime as dt
import re

import bcrypt
import jwt

from .settings import get_settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("at least 10 characters")
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password):
        problems.append("upper- and lower-case letters")
    if not re.search(r"\d", password):
        problems.append("a digit")
    return problems


def create_access_token(user_id: int, tenant_id: int, role: str, minutes: int | None = None) -> str:
    s = get_settings()
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes or s.access_token_minutes)
    payload = {"sub": str(user_id), "tid": tenant_id, "role": role, "exp": exp, "typ": "access",
               "iat": dt.datetime.now(dt.timezone.utc)}
    return jwt.encode(payload, s.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(value: str) -> str:
    """Lenient e-mail check (internal domains such as company.local are allowed)."""
    value = (value or "").strip().lower()
    if not EMAIL_RE.match(value):
        raise ValueError("Enter a valid e-mail address")
    return value
