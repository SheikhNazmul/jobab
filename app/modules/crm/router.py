"""Leads, complaints and comment logs on the dashboard."""

import uuid
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.errors import NotFound
from app.db.session import SessionDep
from app.modules.crm import service as crm
from app.modules.crm.models import CommentLog, Complaint, ComplaintStatus, Lead, LeadStatus
from app.modules.users.deps import AuthDep

router = APIRouter(prefix="/api", tags=["crm"])


@router.get("/leads")
async def list_leads(
    auth: AuthDep,
    session: SessionDep,
    status: str = Query(default="", max_length=20),
    limit: int = Query(default=100, ge=1, le=300),
):
    where = [Lead.shop_id == auth.shop.id]
    if status:
        where.append(Lead.status == LeadStatus(status))
    rows = await session.scalars(
        select(Lead).where(*where).order_by(Lead.created_at.desc()).limit(limit)
    )
    return {"items": [crm.lead_dict(lead) for lead in rows]}


@router.patch("/leads/{lead_id}")
async def set_lead(
    lead_id: uuid.UUID,
    auth: AuthDep,
    session: SessionDep,
    status: Literal["new", "contacted", "won", "lost"] = Query(),
):
    lead = await crm.set_lead_status(session, auth.shop.id, lead_id, LeadStatus(status))
    if lead is None:
        raise NotFound("Lead not found.")
    return crm.lead_dict(lead)


@router.get("/complaints")
async def list_complaints(
    auth: AuthDep,
    session: SessionDep,
    status: str = Query(default="", max_length=20),
    limit: int = Query(default=100, ge=1, le=300),
):
    where = [Complaint.shop_id == auth.shop.id]
    if status:
        where.append(Complaint.status == ComplaintStatus(status))
    rows = await session.scalars(
        select(Complaint).where(*where).order_by(Complaint.created_at.desc()).limit(limit)
    )
    return {"items": [crm.complaint_dict(c) for c in rows]}


@router.post("/complaints/{complaint_id}/resolve")
async def resolve_complaint(complaint_id: uuid.UUID, auth: AuthDep, session: SessionDep):
    complaint = await crm.resolve_complaint(session, auth.shop.id, complaint_id)
    if complaint is None:
        raise NotFound("Complaint not found.")
    return crm.complaint_dict(complaint)


@router.get("/comments")
async def list_comments(
    auth: AuthDep, session: SessionDep, limit: int = Query(default=100, ge=1, le=300)
):
    rows = await session.scalars(
        select(CommentLog)
        .where(CommentLog.shop_id == auth.shop.id)
        .order_by(CommentLog.created_at.desc())
        .limit(limit)
    )
    return {"items": [crm.comment_dict(c) for c in rows]}
