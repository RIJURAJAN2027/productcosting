"""Administration: tenant & licence, users, companies, settings, backup / restore."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import get_company, get_current_user, require
from ..models import ROLES, Company, Tenant, User
from ..security import hash_password, password_problems, valid_email
from ..services.backup import export_company, restore_company
from ..services.config_builder import DEFAULT_SETTINGS, UNITS, company_settings
from ..services.demo import create_demo_company, seed_demo_runs

router = APIRouter(prefix="/api", tags=["administration"])


class UserIn(BaseModel):
    email: str

    _check_email = field_validator("email")(lambda v: valid_email(v))
    name: str = ""
    role: str = "viewer"
    password: str | None = None
    company_ids: list[int] | None = None
    is_active: bool = True
    auth_provider: str = "local"


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    password: str | None = None
    company_ids: list[int] | None = None
    is_active: bool | None = None
    unlock: bool | None = None


class CompanyIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str
    currency: str = "INR"
    settings: dict = {}


class CompanyPatch(BaseModel):
    name: str | None = None
    currency: str | None = None
    settings: dict | None = None


class TenantPatch(BaseModel):
    name: str | None = None
    license_plan: str | None = None
    license_max_companies: int | None = None
    license_max_users: int | None = None
    license_expires: dt.date | None = None
    settings: dict | None = None


def _user_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active,
            "company_ids": u.company_ids, "auth_provider": u.auth_provider,
            "last_login": u.last_login.isoformat() if u.last_login else None, "locked": u.failed_logins >= 8}


# ----------------------------------------------------------------------- tenant / licence
@router.get("/tenant")
def get_tenant(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.get(Tenant, user.tenant_id)
    n_users = db.scalar(select(func.count(User.id)).where(User.tenant_id == t.id, User.is_active.is_(True)))
    n_comp = db.scalar(select(func.count(Company.id)).where(Company.tenant_id == t.id))
    return {"id": t.id, "name": t.name, "slug": t.slug, "license_plan": t.license_plan,
            "license_max_companies": t.license_max_companies, "license_max_users": t.license_max_users,
            "license_expires": t.license_expires.isoformat() if t.license_expires else None,
            "usage": {"users": n_users, "companies": n_comp}, "settings": t.settings or {}}


@router.patch("/tenant")
def patch_tenant(body: TenantPatch, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    t = db.get(Tenant, user.tenant_id)
    data = body.model_dump(exclude_unset=True)
    licence_fields = {"license_plan", "license_max_companies", "license_max_users", "license_expires"}
    if licence_fields & set(data) and not user.is_superadmin:
        raise HTTPException(403, "Only the vendor (super-admin) can change licence terms.")
    before = audit.row_dict(t)
    for k, v in data.items():
        setattr(t, k, v)
    audit.log(db, user, "update", "tenant", t.id, None, changes=audit.diff(before, audit.row_dict(t)))
    db.commit()
    return get_tenant(user, db)


# ----------------------------------------------------------------------- users
@router.get("/users")
def list_users(user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    return [_user_dict(u) for u in db.scalars(select(User).where(User.tenant_id == user.tenant_id)
                                              .order_by(User.email)).all()]


@router.post("/users", status_code=201)
def create_user(body: UserIn, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    t = db.get(Tenant, user.tenant_id)
    n = db.scalar(select(func.count(User.id)).where(User.tenant_id == t.id, User.is_active.is_(True)))
    if n >= t.license_max_users:
        raise HTTPException(402, f"Licence allows {t.license_max_users} active users.")
    if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(409, "A user with this email already exists")
    if body.auth_provider == "local":
        if not body.password:
            raise HTTPException(422, "A password is required for local users")
        problems = password_problems(body.password)
        if problems:
            raise HTTPException(422, "Password needs " + ", ".join(problems))
    u = User(tenant_id=user.tenant_id, email=body.email.lower(), name=body.name, role=body.role,
             password_hash=hash_password(body.password) if body.password else None, company_ids=body.company_ids,
             is_active=body.is_active, auth_provider=body.auth_provider)
    db.add(u)
    db.flush()
    audit.log(db, user, "create", "user", u.id, None, email=u.email, role=u.role)
    db.commit()
    return _user_dict(u)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None or u.tenant_id != user.tenant_id:
        raise HTTPException(404, "User not found")
    data = body.model_dump(exclude_unset=True)
    if "role" in data and data["role"] not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if u.id == user.id and (data.get("role", "admin") != "admin" or data.get("is_active") is False):
        raise HTTPException(409, "You cannot remove your own admin rights or deactivate yourself.")
    if data.get("password"):
        problems = password_problems(data["password"])
        if problems:
            raise HTTPException(422, "Password needs " + ", ".join(problems))
        u.password_hash = hash_password(data.pop("password"))
    data.pop("password", None)
    if data.pop("unlock", None):
        u.failed_logins = 0
    before = _user_dict(u)
    for k, v in data.items():
        setattr(u, k, v)
    audit.log(db, user, "update", "user", u.id, None, changes=audit.diff(before, _user_dict(u)))
    db.commit()
    return _user_dict(u)


# ----------------------------------------------------------------------- companies
def company_dict(c: Company) -> dict:
    return {"id": c.id, "code": c.code, "name": c.name, "currency": c.currency, "is_demo": c.is_demo,
            "settings": company_settings(c)}


@router.get("/companies")
def list_companies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(Company).where(Company.tenant_id == user.tenant_id).order_by(Company.name)
    rows = db.scalars(q).all()
    if user.company_ids and user.role != "admin":
        rows = [c for c in rows if c.id in user.company_ids]
    return [company_dict(c) for c in rows]


@router.post("/companies", status_code=201)
def create_company(body: CompanyIn, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    t = db.get(Tenant, user.tenant_id)
    n = db.scalar(select(func.count(Company.id)).where(Company.tenant_id == t.id, Company.is_demo.is_(False)))
    if n >= t.license_max_companies:
        raise HTTPException(402, f"Licence allows {t.license_max_companies} companies.")
    if db.scalar(select(Company).where(Company.tenant_id == t.id, Company.code == body.code)):
        raise HTTPException(409, "Company code already exists")
    settings = dict(DEFAULT_SETTINGS)
    settings.update(body.settings or {})
    c = Company(tenant_id=t.id, code=body.code, name=body.name, currency=body.currency, settings=settings)
    db.add(c)
    db.flush()
    audit.log(db, user, "create", "company", c.id, c.id, code=c.code, name=c.name)
    db.commit()
    return company_dict(c)


@router.get("/companies/{company_id}")
def get_company_info(company: Company = Depends(get_company)):
    return company_dict(company)


@router.patch("/companies/{company_id}")
def update_company(body: CompanyPatch, company: Company = Depends(get_company),
                   user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    before = company_dict(company)
    data = body.model_dump(exclude_unset=True)
    if "settings" in data:
        s = dict(company.settings or {})
        s.update(data.pop("settings") or {})
        if s.get("display_unit") and s["display_unit"] not in UNITS:
            raise HTTPException(422, f"display_unit must be one of {', '.join(UNITS)}")
        company.settings = s
    for k, v in data.items():
        setattr(company, k, v)
    audit.log(db, user, "update", "company", company.id, company.id,
              changes=audit.diff(before, company_dict(company)))
    db.commit()
    return company_dict(company)


@router.delete("/companies/{company_id}")
def delete_company(company: Company = Depends(get_company), user: User = Depends(require("admin")),
                   db: Session = Depends(get_db)):
    audit.log(db, user, "delete", "company", company.id, None, code=company.code, name=company.name)
    db.delete(company)
    db.commit()
    return {"ok": True}


@router.post("/demo-company", status_code=201)
def create_demo(user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    t = db.get(Tenant, user.tenant_id)
    c = create_demo_company(db, t, user)
    db.commit()
    seed_demo_runs(db, c, user, None)
    return company_dict(c)


# ----------------------------------------------------------------------- backup / restore
@router.get("/companies/{company_id}/backup")
def backup(company: Company = Depends(get_company), user: User = Depends(require("admin")),
           db: Session = Depends(get_db)):
    data = export_company(db, company)
    audit.log(db, user, "backup", "company", company.id, company.id, bytes=len(data))
    db.commit()
    name = f"backup_{company.code}_{dt.date.today():%Y%m%d}.pcbak.gz"
    return Response(data, media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/restore", status_code=201)
async def restore(file: UploadFile = File(...), new_code: str | None = Form(None),
                  user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    t = db.get(Tenant, user.tenant_id)
    c = restore_company(db, t, await file.read(), user, new_code)
    return company_dict(c)
