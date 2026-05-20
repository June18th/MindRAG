"""Conversation service - session management, history, persistence."""
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import orjson
from sqlalchemy import desc, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis import get_redis
from models.conversation import Conversation
from models.conversation_session import ConversationSession
from models.user import User
from services.auth import get_user_by_id

logger = logging.getLogger(__name__)

SESSION_TTL = 7 * 24 * 3600  # 7 days
HISTORY_TTL = 7 * 24 * 3600
MAX_HISTORY_MESSAGES = 20


# --- Conversation Sessions ---

async def get_conversation_sessions(user_id: int, db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(ConversationSession)
        .where(ConversationSession.user_id == user_id)
        .order_by(desc(ConversationSession.updated_at))
    )
    sessions = result.scalars().all()
    return [
        {
            "id": s.id,
            "conversationId": s.conversation_id,
            "title": s.title or "新对话",
            "status": s.status,
            "createdAt": s.created_at.strftime("%Y-%m-%dT%H:%M:%S") if s.created_at else None,
            "updatedAt": s.updated_at.strftime("%Y-%m-%dT%H:%M:%S") if s.updated_at else None,
        }
        for s in sessions
    ]


async def create_conversation_session(user_id: int, db: AsyncSession) -> dict:
    user = await get_user_by_id(db, user_id)
    if not user:
        from core.exceptions import AppException
        raise AppException(404, "User not found")

    conversation_id = str(uuid.uuid4())
    session = ConversationSession(
        user_id=user.id,
        conversation_id=conversation_id,
        title="新对话",
        status="ACTIVE",
    )
    db.add(session)
    await db.flush()

    # Update Redis current conversation
    r = await get_redis()
    await r.setex(f"user:{user_id}:current_conversation", SESSION_TTL, conversation_id)

    return {
        "conversationId": conversation_id,
        "title": "新对话",
        "status": "ACTIVE",
        "createdAt": session.created_at.strftime("%Y-%m-%dT%H:%M:%S") if session.created_at else None,
        "updatedAt": session.updated_at.strftime("%Y-%m-%dT%H:%M:%S") if session.updated_at else None,
    }


async def ensure_conversation_session(user_id: int, conversation_id: str, title: str | None, db: AsyncSession) -> None:
    result = await db.execute(
        select(ConversationSession).where(ConversationSession.conversation_id == conversation_id)
    )
    if result.scalar_one_or_none():
        return

    user = await get_user_by_id(db, user_id)
    if not user:
        from core.exceptions import AppException
        raise AppException(404, "User not found")

    session = ConversationSession(
        user_id=user.id,
        conversation_id=conversation_id,
        title=title or "新对话",
        status="ACTIVE",
    )
    db.add(session)


async def archive_conversation_session(conversation_id: str, db: AsyncSession) -> None:
    result = await db.execute(
        select(ConversationSession).where(ConversationSession.conversation_id == conversation_id)
    )
    session = result.scalar_one_or_none()
    if session:
        session.status = "ARCHIVED"


async def unarchive_conversation_session(conversation_id: str, db: AsyncSession) -> None:
    result = await db.execute(
        select(ConversationSession).where(ConversationSession.conversation_id == conversation_id)
    )
    session = result.scalar_one_or_none()
    if session:
        session.status = "ACTIVE"


async def switch_current_conversation(user_id: int, conversation_id: str, db: AsyncSession) -> None:
    result = await db.execute(
        select(ConversationSession).where(ConversationSession.conversation_id == conversation_id)
    )
    if not result.scalar_one_or_none():
        from core.exceptions import AppException
        raise AppException(404, "对话不存在")

    r = await get_redis()
    await r.setex(f"user:{user_id}:current_conversation", SESSION_TTL, conversation_id)


# --- Conversation History ---

async def get_or_create_conversation_id(user_id: str) -> str:
    r = await get_redis()
    key = f"user:{user_id}:current_conversation"
    cid = await r.get(key)
    if cid:
        return cid

    cid = str(uuid.uuid4())
    await r.setex(key, SESSION_TTL, cid)
    return cid


async def get_conversation_history(conversation_id: str) -> list[dict]:
    r = await get_redis()
    key = f"conversation:{conversation_id}"
    raw = await r.get(key)
    if raw:
        try:
            return orjson.loads(raw)
        except Exception:
            return []
    return []


async def append_to_conversation_history(conversation_id: str, role: str, content: str) -> None:
    r = await get_redis()
    key = f"conversation:{conversation_id}"
    history = await get_conversation_history(conversation_id)
    history.append({"role": role, "content": content, "timestamp": datetime.now().isoformat()})
    if len(history) > MAX_HISTORY_MESSAGES:
        history = history[-MAX_HISTORY_MESSAGES:]
    await r.setex(key, HISTORY_TTL, orjson.dumps(history).decode())


async def get_messages_by_conversation_id(conversation_id: str, db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.conversation_id == conversation_id)
        .order_by(Conversation.timestamp.asc())
    )
    conversations = result.scalars().all()
    messages = []
    for c in conversations:
        ts = c.timestamp.strftime("%Y-%m-%dT%H:%M:%S") if c.timestamp else None
        messages.append({"role": "user", "content": c.question, "timestamp": ts})
        if c.answer:
            messages.append({"role": "assistant", "content": c.answer, "timestamp": ts})
    return messages


async def persist_conversation(
    db: AsyncSession,
    user_id: int,
    question: str,
    answer: str,
    conversation_id: str | None = None,
    reference_mappings: dict | None = None,
) -> None:
    conv = Conversation(
        user_id=user_id,
        question=question,
        answer=answer,
        conversation_id=conversation_id,
        reference_mappings_json=orjson.dumps(reference_mappings).decode() if reference_mappings else None,
    )
    db.add(conv)

    # Update session title if default
    if conversation_id:
        result = await db.execute(
            select(ConversationSession).where(ConversationSession.conversation_id == conversation_id)
        )
        session = result.scalar_one_or_none()
        if session and session.title == "新对话":
            session.title = question[:50]


async def get_conversations(
    db: AsyncSession,
    username: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[Conversation]:
    conditions = [Conversation.user.has(User.username == username)]
    if start_time:
        conditions.append(Conversation.timestamp >= start_time)
    if end_time:
        conditions.append(Conversation.timestamp <= end_time)

    result = await db.execute(
        select(Conversation)
        .where(and_(*conditions))
        .order_by(Conversation.timestamp.asc())
    )
    return list(result.scalars().all())


def to_message_history(conversations: list[Conversation], include_ai: bool = True) -> list[dict]:
    messages = []
    for c in conversations:
        messages.append({"role": "user", "content": c.question, "timestamp": c.timestamp.isoformat() if c.timestamp else None})
        if include_ai and c.answer:
            messages.append({"role": "assistant", "content": c.answer, "timestamp": c.timestamp.isoformat() if c.timestamp else None})
    return messages
