"""The Master sheet (business areas, GL accounts and cost centres in the workbook's Masters layout):
view, edit in the browser, Excel download and upload (merge), and codes new in uploaded trial balances."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_company, require
from ..models import Company, Period, User
from ..services import master_sheet as ms

router = APIRouter(prefix="/api/companies/{company_id}/master-sheet", tags=["masters"])


@router.get("")
def get_sheet(on: str | None = None, company: Company = Depends(get_company), db: Session = Depends(get_db)):
    """The sheet valid on ``on`` (yyyy-mm-dd), or the latest version of every row when omitted."""
    return ms.read_sheet(db, company, ms.effective_date(on))


class SectionChanges(BaseModel):
    upserts: list[dict[str, Any]] = Field(default_factory=list)
    deletes: list[str] = Field(default_factory=list)


class SheetChanges(BaseModel):
    effective_from: str | None = None     # yyyy-mm-dd, or None / "all" to change rows in place
    ba: SectionChanges = Field(default_factory=SectionChanges)
    gl: SectionChanges = Field(default_factory=SectionChanges)
    cc: SectionChanges = Field(default_factory=SectionChanges)


@router.put("")
def save_changes(body: SheetChanges, company: Company = Depends(get_company), user: User = Depends(require("edit")),
                 db: Session = Depends(get_db)):
    """Manual edits from the browser: rows to add or change and codes to remove, per section.  An empty
    value clears the field."""
    eff = ms.effective_date(body.effective_from)
    out = {}
    # business areas first: cost-centre plants are derived from them
    for sec in ("ba", "gl", "cc"):
        ch: SectionChanges = getattr(body, sec)
        if ch.upserts or ch.deletes:
            out[sec] = ms.apply_changes(db, company, user, sec, ch.upserts, ch.deletes, eff, blank_keeps=False)
            db.flush()
    db.commit()
    return {"effective_from": body.effective_from, "sections": out}


@router.post("/import")
async def import_sheet(file: UploadFile = File(...), effective_from: str | None = Form(None),
                       remove_missing: bool = Form(False), dry_run: bool = Form(True),
                       create_setup: bool = Form(False),
                       company: Company = Depends(get_company), user: User = Depends(require("edit")),
                       db: Session = Depends(get_db)):
    """Upload the master sheet (full or add-ons only).  ``dry_run`` (default) previews the changes;
    ``create_setup`` also creates the plants and product groups the business areas name."""
    data = await file.read()
    return ms.preview_or_import(db, company, user, data, file.filename or "master.xlsx",
                                ms.effective_date(effective_from), remove_missing, dry_run, create_setup)


@router.get("/export")
def export_sheet(on: str | None = None, company: Company = Depends(get_company), db: Session = Depends(get_db)):
    data = ms.export_xlsx(db, company, ms.effective_date(on))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="master_{company.code}.xlsx"'})


@router.get("/pending")
def pending(period_id: int, on: str | None = None, company: Company = Depends(get_company),
            db: Session = Depends(get_db)):
    """Codes in the period's uploaded BA / CC trial balances that the master does not have yet."""
    period = db.get(Period, period_id)
    if period is None or period.company_id != company.id:
        raise HTTPException(404, "Period not found")
    eff = ms.effective_date(on)
    return ms.pending_from_uploads(db, company, period, eff if eff else period.end_date)
