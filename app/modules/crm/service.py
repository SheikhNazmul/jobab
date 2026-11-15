"""Leads, complaints and comment logs: small write helpers used by the agent,
the webhook worker and the dashboard.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import utcnow
from app.modules.crm.models import (
    CommentLog,
    Complaint,
    ComplaintStatus,
    Lead,
    LeadStatus,
)


async def save_lead(
    session: AsyncSession,
    shop_id: uuid.UUID,
    *,
    name: str = "",
    phone: str = "",
    interest: str = "",
    conversation_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
) -> Lead:
    lead = Lead(
        shop_id=shop_id,
        conversation_id=conversation_id,
        contact_id=contact_id,
        name=name[:160],
        phone=phone[:32],
        interest=interest[:2000],
    )
    session.add(lead)
    await session.flush()
    return lead


async def open_complaint(
    session: AsyncSession,
    shop_id: uuid.UUID,
    summary: str,
    *,
    conversation_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
) -> Complaint:
    complaint = Complaint(
        shop_id=shop_id,
        conversation_id=conversation_id,
        contact_id=contact_id,
        summary=summary[:2000],
    )
    session.add(complaint)
    await session.flush()
    return complaint


async def resolve_complaint(
    session: AsyncSession, shop_id: uuid.UUID, complaint_id: uuid.UUID
) -> Complaint | None:
    complaint = await session.scalar(
        select(Complaint).where(Complaint.id == complaint_id, Complaint.shop_id == shop_id)
    )
    if complaint is None:
        return None
    complaint.status = ComplaintStatus.RESOLVED
    complaint.resolved_at = utcnow()
    await session.flush()
    return complaint


async def set_lead_status(
    session: AsyncSession, shop_id: uuid.UUID, lead_id: uuid.UUID, status: LeadStatus
) -> Lead | None:
    lead = await session.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.shop_id == shop_id)
    )
    if lead is None:
        return None
    lead.status = status
    await session.flush()
    return lead


def lead_dict(lead: Lead) -> dict[str, Any]:
    return {
        "id": str(lead.id),
        "name": lead.name,
        "phone": lead.phone,
        "interest": lead.interest,
        "status": lead.status.value,
        "conversation_id": str(lead.conversation_id) if lead.conversation_id else None,
        "created_at": lead.created_at,
    }


def complaint_dict(complaint: Complaint) -> dict[str, Any]:
    return {
        "id": str(complaint.id),
        "summary": complaint.summary,
        "status": complaint.status.value,
        "conversation_id": str(complaint.conversation_id) if complaint.conversation_id else None,
        "created_at": complaint.created_at,
        "resolved_at": complaint.resolved_at,
    }


def comment_dict(comment: CommentLog) -> dict[str, Any]:
    return {
        "id": str(comment.id),
        "post_id": comment.post_id,
        "author_name": comment.author_name,
        "text": comment.text,
        "reply_text": comment.reply_text,
        "private_text": comment.private_text,
        "status": comment.status,
        "created_at": comment.created_at,
    }
