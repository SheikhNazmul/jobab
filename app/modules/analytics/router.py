"""Dashboard overview: today's numbers and a two-week trend."""

from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import case, func, select

from app.core.utils import utcnow
from app.db.session import SessionDep
from app.modules.crm.models import Complaint, ComplaintStatus, Lead
from app.modules.inbox.models import Conversation, Message, Sender
from app.modules.orders.models import Order, OrderStatus
from app.modules.shops.service import credits_left
from app.modules.users.deps import AuthDep

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

LIVE_ORDER = Order.status.notin_([OrderStatus.CANCELLED, OrderStatus.RETURNED])


@router.get("/overview")
async def overview(auth: AuthDep, session: SessionDep):
    shop_id = auth.shop.id
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    since = day_start - timedelta(days=13)

    async def count(stmt) -> int:
        return (await session.scalar(stmt)) or 0

    totals = {
        "orders_today": await count(
            select(func.count()).select_from(Order).where(
                Order.shop_id == shop_id, Order.created_at >= day_start)
        ),
        "revenue_today": float(
            await count(
                select(func.coalesce(func.sum(Order.total), 0)).where(
                    Order.shop_id == shop_id, Order.created_at >= day_start, LIVE_ORDER)
            )
        ),
        "messages_today": await count(
            select(func.count()).select_from(Message).where(
                Message.shop_id == shop_id, Message.created_at >= day_start)
        ),
        "ai_replies_today": await count(
            select(func.count()).select_from(Message).where(
                Message.shop_id == shop_id, Message.sender == Sender.AI,
                Message.created_at >= day_start)
        ),
        "needs_human": await count(
            select(func.count()).select_from(Conversation).where(
                Conversation.shop_id == shop_id, Conversation.needs_human.is_(True))
        ),
        "open_complaints": await count(
            select(func.count()).select_from(Complaint).where(
                Complaint.shop_id == shop_id, Complaint.status == ComplaintStatus.OPEN)
        ),
        "new_leads_today": await count(
            select(func.count()).select_from(Lead).where(
                Lead.shop_id == shop_id, Lead.created_at >= day_start)
        ),
        "orders_to_review": await count(
            select(func.count()).select_from(Order).where(
                Order.shop_id == shop_id, Order.needs_review.is_(True))
        ),
        "credits_left": credits_left(auth.shop),
        "credits_total": auth.shop.credits_total,
        "plan": auth.shop.plan,
        "period_ends_at": auth.shop.period_ends_at,
    }

    day = func.date_trunc("day", Message.created_at)
    msg_rows = {
        r.day.date().isoformat(): r.n
        for r in await session.execute(
            select(day.label("day"), func.count().label("n"))
            .where(Message.shop_id == shop_id, Message.created_at >= since,
                   Message.sender == Sender.CUSTOMER)
            .group_by(day)
        )
    }
    oday = func.date_trunc("day", Order.created_at)
    order_rows = {
        r.day.date().isoformat(): (r.n, float(r.rev))
        for r in await session.execute(
            select(
                oday.label("day"), func.count().label("n"),
                func.coalesce(func.sum(case((LIVE_ORDER, Order.total), else_=0)), 0).label("rev"),
            )
            .where(Order.shop_id == shop_id, Order.created_at >= since)
            .group_by(oday)
        )
    }

    days = []
    for i in range(13, -1, -1):
        key = (day_start - timedelta(days=i)).date().isoformat()
        orders_n, revenue = order_rows.get(key, (0, 0.0))
        days.append({
            "date": key,
            "customer_messages": msg_rows.get(key, 0),
            "orders": orders_n,
            "revenue": revenue,
        })
    return {"totals": totals, "days": days}
