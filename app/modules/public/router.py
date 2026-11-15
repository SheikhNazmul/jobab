"""Open endpoints used by the marketing homepage (no sign-in)."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field

from app.core.ratelimit import enforce
from app.core.utils import clean_text, client_ip
from app.db.session import SessionDep
from app.modules.public.models import DemoRequest

router = APIRouter(prefix="/api/public", tags=["public"])


class DemoRequestIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    business: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=32)
    email: EmailStr | str = Field(default="", max_length=255)
    message: str = Field(default="", max_length=1000)


@router.post("/demo", status_code=201)
async def request_demo(body: DemoRequestIn, request: Request, session: SessionDep):
    ip = client_ip(request)
    enforce(f"demo:{ip}", 5, 3600,
            "Thanks! You have already sent a few requests. We will call you.")
    session.add(
        DemoRequest(
            name=clean_text(body.name, 160),
            business=clean_text(body.business, 160),
            phone=clean_text(body.phone, 32),
            email=clean_text(str(body.email), 255),
            message=clean_text(body.message, 1000),
            ip=ip[:64],
        )
    )
    return {"ok": True}
