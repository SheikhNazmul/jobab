"""The reply loop: turn a customer's latest messages into one assistant reply.

Called by the webhook worker and the website-chat endpoint after a customer
message has been stored. It decides whether the AI should answer, builds the
context, lets the model use tools, and records the reply. Delivering the reply
to the customer is the caller's job, because only the caller holds the channel.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.utils import utcnow
from app.modules.agent import llm
from app.modules.agent.prompt import MAX_HISTORY_MESSAGES, build_system_prompt, handoff_message
from app.modules.agent.tools import AgentOutcome, ToolContext, run_tool, tool_schemas
from app.modules.channels.models import Channel
from app.modules.inbox import service as inbox
from app.modules.inbox.models import (
    Conversation,
    Direction,
    Message,
    Sender,
)
from app.modules.shops.models import Shop
from app.modules.shops.schemas import ShopSettings
from app.modules.shops.service import can_use_ai, spend_credit

log = logging.getLogger("app.agent")
settings = get_settings()

MAX_TOOL_ROUNDS = 5


@dataclass
class ReplyResult:
    reply: Message | None = None
    outcome: AgentOutcome = field(default_factory=AgentOutcome)
    handed_off_text: str = ""
    skipped: str = ""


async def generate_reply(
    session: AsyncSession,
    shop: Shop,
    conversation: Conversation,
    contact: Any,
    channel: Channel,
) -> ReplyResult:
    settings_model = ShopSettings.model_validate(shop.settings or {})

    if inbox.ai_is_muted(conversation) or conversation.needs_human:
        return ReplyResult(skipped="muted")
    if not settings.AI_ENABLED or not settings_model.ai.enabled:
        return ReplyResult(skipped="ai_off")
    if not settings.llm_configured:
        return ReplyResult(skipped="not_configured")
    if await _hit_reply_cap(session, conversation, settings_model.ai.max_replies_per_hour):
        log.info("Reply cap hit on conversation %s", conversation.id)
        return ReplyResult(skipped="rate_capped")

    # One credit per reply, taken up front. If none are left, hand to a person.
    if not can_use_ai(shop) or not await spend_credit(
        session, shop.id, "ai_reply", conversation_id=conversation.id
    ):
        await _handoff(session, conversation, "No AI credits left")
        return ReplyResult(
            outcome=AgentOutcome(handoff=True, handoff_reason="No AI credits left"),
            handed_off_text="",
            skipped="no_credits",
        )

    history = await _history(session, conversation)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(shop, settings_model)},
        *history,
    ]
    outcome = AgentOutcome()
    ctx = ToolContext(
        session=session, shop=shop, settings=settings_model, conversation=conversation,
        contact=contact, outcome=outcome, channel_type=channel.type.value,
    )
    schemas = tool_schemas(settings_model)

    prompt_tokens = completion_tokens = 0
    final_text = ""
    try:
        for _round in range(MAX_TOOL_ROUNDS):
            answer = await llm.chat(messages, tools=schemas)
            usage = answer.get("usage") or {}
            prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            message = answer["message"]
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                final_text = (message.get("content") or "").strip()
                break
            # Keep the assistant's tool-call turn, then answer each call.
            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                result = await _run_one(ctx, call)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(result, default=str),
                })
            if outcome.handoff:
                # A tool asked for a person. Do one more round so the model can
                # say goodbye, then stop offering tools.
                schemas = []
        else:
            final_text = ""
    except llm.LLMError as exc:
        log.warning("AI reply failed on conversation %s: %s", conversation.id, exc)
        await _handoff(session, conversation, "AI could not reply")
        return ReplyResult(
            outcome=AgentOutcome(handoff=True, handoff_reason="AI could not reply"),
            skipped="llm_error",
        )

    if not final_text:
        final_text = handoff_message(settings_model)
        outcome.handoff = True
        outcome.handoff_reason = outcome.handoff_reason or "AI had no answer"

    reply = await inbox.add_message(
        session, conversation,
        direction=Direction.OUT, sender=Sender.AI, text=final_text,
        meta={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    )

    if outcome.handoff:
        await _handoff(session, conversation, outcome.handoff_reason)

    return ReplyResult(reply=reply, outcome=outcome)


async def _run_one(ctx: ToolContext, call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") or {}
    name = function.get("name", "")
    try:
        args = json.loads(function.get("arguments") or "{}")
        if not isinstance(args, dict):
            args = {}
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": "Arguments were not valid."}
    return await run_tool(ctx, name, args)


async def _history(session: AsyncSession, conversation: Conversation) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id,
               Message.direction != Direction.INTERNAL)
        .order_by(Message.seq.desc())
        .limit(MAX_HISTORY_MESSAGES)
    )
    history: list[dict[str, Any]] = []
    for message in reversed(list(rows)):
        text = message.text
        if not text and message.attachments:
            kinds = ", ".join(a.get("kind", "file") for a in message.attachments)
            text = f"[customer sent: {kinds}]"
        if not text:
            continue
        role = "user" if message.direction == Direction.IN else "assistant"
        history.append({"role": role, "content": text})
    return history


async def _hit_reply_cap(session: AsyncSession, conversation: Conversation, limit: int) -> bool:
    since = utcnow() - timedelta(hours=1)
    count = await session.scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.sender == Sender.AI,
            Message.created_at >= since,
        )
    )
    return (count or 0) >= limit


async def _handoff(session: AsyncSession, conversation: Conversation, reason: str) -> None:
    await inbox.set_handoff(session, conversation, reason)
    conversation.ai_enabled = False
