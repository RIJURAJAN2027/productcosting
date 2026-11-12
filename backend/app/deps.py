"""FastAPI dependencies: current user, role checks and company/period access."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import CalcRun, Company, Period, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)

ROLE_RANK = {"viewer": 0, "approver": 1, "analyst": 1, "admin": 3}
# what each role may do
PERMISSIONS = {
    "view": {"viewer", "analyst", "approver", "admin"},
    "edit": {"analyst", "admin"},               # uploads, masters, adjustments, calculations
    "approve": {"approver", "admin"},           # approve / reject / publish runs
    "admin": {"admin"},                         # users, companies, licence, backup
}


def get_current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                     db: Session = Depends(get_db)) -> User:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_token(cred.credentials)
        if payload.get("typ", "access") != "access":
            raise ValueError("not an access token")
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User disabled or not found")
    return user


def require(permission: str):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.is_superadmin or user.role in PERMISSIONS[permission]:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role ({user.role}) may not {permission}.")
    return dep


def can(user: User, permission: str) -> bool:
    return user.is_superadmin or user.role in PERMISSIONS[permission]


def get_company(company_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Company:
    company = db.get(Company, company_id)
    if company is None or (company.tenant_id != user.tenant_id and not user.is_superadmin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    if user.company_ids and company.id not in user.company_ids and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this company")
    return company


def get_period(period_id: int, company: Company = Depends(get_company), db: Session = Depends(get_db)) -> Period:
    period = db.get(Period, period_id)
    if period is None or period.company_id != company.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")
    return period


def ensure_open(period: Period) -> None:
    if period.status == "locked":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Period {period.code} is locked.")


def get_run_for_user(run_id: int, user: User, db: Session) -> CalcRun:
    run = db.get(CalcRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    company = db.get(Company, run.company_id)
    if company is None or (company.tenant_id != user.tenant_id and not user.is_superadmin):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run
