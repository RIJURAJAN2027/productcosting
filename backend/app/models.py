"""Database model.

Hierarchy: Tenant -> Company -> (masters, periods) -> uploads / adjustments / runs.
Masters carry ``effective_from`` / ``effective_to`` so a calculation uses the
master version valid on the period's end date.  Every change is audited.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = ("admin", "analyst", "approver", "viewer")
RUN_STATUSES = ("draft", "submitted", "approved", "rejected", "published", "superseded", "failed")


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now,
                                                    server_default=func.now())


# ------------------------------------------------------------------ tenancy & users
class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    license_plan: Mapped[str] = mapped_column(String(40), default="standard")
    license_max_companies: Mapped[int] = mapped_column(Integer, default=5)
    license_max_users: Mapped[int] = mapped_column(Integer, default=25)
    license_expires: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    companies: Mapped[list["Company"]] = relationship(back_populates="tenant")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)   # None = all companies of tenant
    last_login: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    # engine & display settings: calc_unit_divisor, display_unit, number_format, decimals,
    # fiscal_year_start_month, com_code, cor_code, pre_categories, meta (pbt_line, sales_line ...)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    tenant: Mapped[Tenant] = relationship(back_populates="companies")


# ------------------------------------------------------------------ masters (versioned)
class VersionedMixin:
    effective_from: Mapped[dt.date] = mapped_column(Date, default=dt.date(2000, 1, 1))
    effective_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)


class Plant(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "plants"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_reporting: Mapped[bool] = mapped_column(Boolean, default=True)


class ProductGroup(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "product_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), default="")
    family: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class BusinessArea(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "business_areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    plant_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pg_code: Mapped[str | None] = mapped_column(String(40), nullable=True)   # PG, COM, COR or other
    family: Mapped[str | None] = mapped_column(String(100), nullable=True)


class GLAccount(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "gl_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    fs_description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gl_type: Mapped[str] = mapped_column(String(20), default="NONPRE")     # NONPRE | PRE | EXCLUDED
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CostCentre(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "cost_centres"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    business_area: Mapped[str | None] = mapped_column(String(60), nullable=True)
    plant_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    function: Mapped[str | None] = mapped_column(String(60), nullable=True)   # PG code or service function


class Category(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    sign: Mapped[int] = mapped_column(Integer, default=1)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class PreFunction(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "pre_functions"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200), default="")
    group: Mapped[str] = mapped_column(String(20), default="factory")        # factory | corporate
    mode: Mapped[str] = mapped_column(String(20), default="plant")           # plant | pooled | custom


class AllocationRule(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "allocation_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    dataset: Mapped[str] = mapped_column(String(10), default="NONPRE")
    category: Mapped[str] = mapped_column(String(200))
    pool: Mapped[str] = mapped_column(String(60))                            # COM | COR | PRE function
    mode: Mapped[str] = mapped_column(String(20), default="plant")
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)             # basis, basis_cells, legs ...
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class PnlLine(VersionedMixin, TimestampMixin, Base):
    __tablename__ = "pnl_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(300))
    expr: Mapped[str] = mapped_column(Text, default="0")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)   # plant/cell/total exprs, additive, pct_base, hidden


class ReportTemplate(TimestampMixin, Base):
    __tablename__ = "report_templates"
    __table_args__ = (UniqueConstraint("company_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    rows: Mapped[list] = mapped_column(JSON, default=list)


# ------------------------------------------------------------------ periods & inputs
class Period(TimestampMixin, Base):
    __tablename__ = "periods"
    __table_args__ = (UniqueConstraint("company_id", "code", "scenario"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))                      # e.g. 2026-09
    label: Mapped[str] = mapped_column(String(120), default="")
    scenario: Mapped[str] = mapped_column(String(20), default="actual")  # actual | budget | forecast
    basis: Mapped[str] = mapped_column(String(10), default="YTD")      # YTD | MONTH
    fiscal_year: Mapped[str | None] = mapped_column(String(20), nullable=True)
    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[dt.date] = mapped_column(Date)
    months: Mapped[int] = mapped_column(Integer, default=1)            # months covered (for BEP per month)
    status: Mapped[str] = mapped_column(String(20), default="open")    # open | locked
    locked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Upload(TimestampMixin, Base):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    period_id: Mapped[int | None] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"), index=True,
                                                  nullable=True)
    kind: Mapped[str] = mapped_column(String(30))        # BA_TB | CC_TB | MIS | LEGACY_WORKBOOK | MASTERS
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="validated")   # validated | warnings | errors
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    issues: Mapped[list] = mapped_column(JSON, default=list)


class TBLine(Base):
    __tablename__ = "tb_lines"
    __table_args__ = (Index("ix_tb_lines_upload", "upload_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"))
    gl: Mapped[str] = mapped_column(String(60))
    gl_desc: Mapped[str] = mapped_column(String(300), default="")
    dim: Mapped[str] = mapped_column(String(60))
    amount: Mapped[float] = mapped_column(Float)


class AdjustmentRule(TimestampMixin, Base):
    __tablename__ = "adjustment_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    period_id: Mapped[int | None] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"), nullable=True)
    type: Mapped[str] = mapped_column(String(30))
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class McAdjustment(TimestampMixin, Base):
    __tablename__ = "mc_adjustments"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"))
    plant_code: Mapped[str] = mapped_column(String(40))
    pg_code: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10), default="amount")    # amount | mc_pct
    value: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class DriverValue(TimestampMixin, Base):
    __tablename__ = "driver_values"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    period_id: Mapped[int | None] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"), nullable=True)
    driver: Mapped[str] = mapped_column(String(100))
    plant_code: Mapped[str] = mapped_column(String(40))
    pg_code: Mapped[str] = mapped_column(String(40))
    value: Mapped[float] = mapped_column(Float)


class MisLine(TimestampMixin, Base):
    __tablename__ = "mis_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(200))
    expr: Mapped[str] = mapped_column(Text, default="0")       # P&L expression compared with the MIS value
    mis_value: Mapped[float] = mapped_column(Float, default=0.0)
    compare: Mapped[str] = mapped_column(String(20), default="sum")   # sum: mis + pnl = 0 ; difference: mis - pnl
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------------ runs & results
class CalcRun(TimestampMixin, Base):
    __tablename__ = "calc_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    started_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    inputs_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    controls: Mapped[list] = mapped_column(JSON, default=list)
    control_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunValue(Base):
    __tablename__ = "run_values"
    __table_args__ = (Index("ix_run_values_run_line", "run_id", "line"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("calc_runs.id", ondelete="CASCADE"))
    line: Mapped[str] = mapped_column(String(120))
    plant: Mapped[str] = mapped_column(String(40))      # plant code or __ALL__
    code: Mapped[str] = mapped_column(String(40))       # PG / COM / __TOTAL__ / COR (memo)
    value: Mapped[float] = mapped_column(Float)         # in rupees (base currency units)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_company_time", "company_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    details: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, server_default=func.now())


MASTER_MODELS = {
    "plants": Plant, "product-groups": ProductGroup, "business-areas": BusinessArea,
    "gl-accounts": GLAccount, "cost-centres": CostCentre, "categories": Category,
    "pre-functions": PreFunction, "allocation-rules": AllocationRule, "pnl-lines": PnlLine,
}
