"""Authentication: email/password login, JWT tokens and optional OpenID Connect SSO."""
from __future__ import annotations

import datetime as dt
import secrets
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import PERMISSIONS, get_current_user
from ..models import Company, Tenant, User, now
from ..security import create_access_token, hash_password, password_problems, valid_email, verify_password
from ..settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])
MAX_FAILED = 8


class LoginIn(BaseModel):
    email: str

    _check_email = field_validator("email")(lambda v: valid_email(v))
    password: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


def user_out(user: User, db: Session) -> dict:
    tenant = db.get(Tenant, user.tenant_id)
    companies = db.scalars(select(Company).where(Company.tenant_id == user.tenant_id).order_by(Company.name)).all()
    if user.company_ids and user.role != "admin":
        companies = [c for c in companies if c.id in user.company_ids]
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role,
            "is_superadmin": user.is_superadmin,
            "permissions": sorted(p for p, roles in PERMISSIONS.items() if user.role in roles or user.is_superadmin),
            "tenant": {"id": tenant.id, "name": tenant.name, "license_plan": tenant.license_plan,
                       "license_expires": tenant.license_expires.isoformat() if tenant.license_expires else None},
            "companies": [{"id": c.id, "code": c.code, "name": c.name, "is_demo": c.is_demo} for c in companies]}


def _check_tenant(db: Session, user: User) -> None:
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(403, "Your organisation's account is disabled.")
    if tenant.license_expires and tenant.license_expires < dt.date.today():
        raise HTTPException(403, "Your organisation's licence has expired. Please contact your administrator.")


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not user.is_active or user.auth_provider != "local":
        raise HTTPException(401, "Invalid email or password")
    if user.failed_logins >= MAX_FAILED:
        raise HTTPException(423, "Account locked after repeated failed logins; ask an administrator to unlock it.")
    if not verify_password(body.password, user.password_hash):
        user.failed_logins += 1
        audit.log(db, user, "login_failed", "user", user.id, ip=request.client.host if request.client else None)
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    _check_tenant(db, user)
    user.failed_logins = 0
    user.last_login = now()
    audit.log(db, user, "login", "user", user.id, ip=request.client.host if request.client else None)
    db.commit()
    return {"access_token": create_access_token(user.id, user.tenant_id, user.role), "token_type": "bearer",
            "user": user_out(user, db)}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return user_out(user, db)


@router.post("/change-password")
def change_password(body: PasswordIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(422, "Password needs " + ", ".join(problems))
    user.password_hash = hash_password(body.new_password)
    audit.log(db, user, "change_password", "user", user.id)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------- SSO (OpenID Connect)
SSO_STATE_MINUTES = 10


def _sso_state() -> str:
    """Signed, short-lived OAuth state. It is stateless, so the callback may reach any server
    process or serverless instance."""
    import jwt as pyjwt
    payload = {"typ": "sso_state", "nonce": secrets.token_urlsafe(16),
               "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=SSO_STATE_MINUTES)}
    return pyjwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def _check_sso_state(state: str) -> bool:
    import jwt as pyjwt
    try:
        claims = pyjwt.decode(state, get_settings().secret_key, algorithms=["HS256"])
    except pyjwt.PyJWTError:
        return False
    return claims.get("typ") == "sso_state"


def _oidc_metadata() -> dict:
    import httpx
    s = get_settings()
    r = httpx.get(s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10)
    r.raise_for_status()
    return r.json()


@router.get("/sso/config")
def sso_config():
    s = get_settings()
    return {"enabled": bool(s.oidc_issuer and s.oidc_client_id), "issuer": s.oidc_issuer}


@router.get("/sso/login")
def sso_login():
    s = get_settings()
    if not (s.oidc_issuer and s.oidc_client_id and s.oidc_redirect_uri):
        raise HTTPException(404, "SSO is not configured")
    meta = _oidc_metadata()
    state = _sso_state()
    q = urllib.parse.urlencode({"response_type": "code", "client_id": s.oidc_client_id,
                                "redirect_uri": s.oidc_redirect_uri, "scope": "openid email profile",
                                "state": state})
    return RedirectResponse(f"{meta['authorization_endpoint']}?{q}")


@router.get("/sso/callback")
def sso_callback(code: str, state: str, db: Session = Depends(get_db)):
    import httpx
    import jwt as pyjwt
    s = get_settings()
    if not _check_sso_state(state):
        raise HTTPException(400, "Invalid or expired SSO state")
    meta = _oidc_metadata()
    tok = httpx.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": s.oidc_redirect_uri,
        "client_id": s.oidc_client_id, "client_secret": s.oidc_client_secret}, timeout=15)
    tok.raise_for_status()
    id_token = tok.json()["id_token"]
    signing_key = pyjwt.PyJWKClient(meta["jwks_uri"]).get_signing_key_from_jwt(id_token)
    claims = pyjwt.decode(id_token, signing_key.key, algorithms=["RS256", "ES256"], audience=s.oidc_client_id,
                          issuer=meta["issuer"])
    email = (claims.get("email") or "").lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None or not user.is_active:
        raise HTTPException(403, "No active user for this SSO identity; ask an administrator to invite you.")
    _check_tenant(db, user)
    user.auth_provider, user.external_id, user.last_login = "oidc", claims.get("sub"), now()
    audit.log(db, user, "login_sso", "user", user.id)
    db.commit()
    token = create_access_token(user.id, user.tenant_id, user.role)
    return RedirectResponse(f"/#/sso?token={token}")
