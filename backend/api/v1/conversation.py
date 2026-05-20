"""Conversation API routes - /api/v1/users/conversation*"""
from datetime import datetime, date

from fastapi import APIRouter, Depends, Header, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.deps import get_current_user
from core.security import extract_user_id, extract_username
from schemas.common import ResponseWrapper
from services.conversation import (
    archive_conversation_session,
    create_conversation_session,
    get_conversation_sessions,
    get_conversations,
    get_messages_by_conversation_id,
    switch_current_conversation,
    to_message_history,
    unarchive_conversation_session,
)

router = APIRouter(tags=["conversation"])


def _parse_datetime(s: str | None, is_end: bool = False) -> datetime | None:
    """Parse flexible datetime string. Mirrors Java ConversationController logic."""
    if not s or not s.strip():
        return None
    s = s.strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Try with appended time
    try:
        if len(s) == 16:
            extra = ":59" if is_end else ":00"
            return datetime.strptime(s + extra, "%Y-%m-%dT%H:%M:%S")
        if len(s) == 13:
            extra = ":59:59" if is_end else ":00:00"
            return datetime.strptime(s + extra, "%Y-%m-%dT%H:%M:%S")
        if len(s) == 10:
            if is_end:
                return datetime.strptime(s, "%Y-%m-%d") + __import__("datetime").timedelta(days=1) - __import__("datetime").timedelta(seconds=1)
            return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise
    raise ValueError(f"无效的时间格式: {s}")


@router.get("/api/v1/users/conversation")
async def get_conversation_history(
    authorization: str = Header(...),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    conversationId: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get conversation history. Mirrors ConversationController.getConversations."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        username = extract_username(token)
        if not username:
            return {"code": 401, "message": "无效的token"}

        if conversationId:
            messages = await get_messages_by_conversation_id(conversationId, db)
        else:
            start = _parse_datetime(start_date, is_end=False)
            end = _parse_datetime(end_date, is_end=True)
            convs = await get_conversations(db, username, start, end)
            messages = to_message_history(convs, include_ai=False)

        return ResponseWrapper(code=200, message="获取对话历史成功", data=messages).model_dump()
    except Exception as e:
        status = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", {"code": 500, "message": str(e)})
        return detail if isinstance(detail, dict) else {"code": status, "message": str(e)}


@router.get("/api/v1/users/conversations")
async def list_sessions(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """List conversation sessions. Mirrors ConversationSessionController.listSessions."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        user_id_str = extract_user_id(token)
        username = extract_username(token)
        if not username:
            return {"code": 401, "message": "无效的token"}

        sessions = await get_conversation_sessions(int(user_id_str), db)
        return ResponseWrapper(code=200, message="获取对话列表成功", data=sessions).model_dump()
    except Exception as e:
        status = getattr(e, "status_code", 500)
        return {"code": status, "message": str(e)}


@router.post("/api/v1/users/conversations")
async def create_session(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """Create new conversation session. Mirrors ConversationSessionController.createSession."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        user_id_str = extract_user_id(token)
        if not user_id_str:
            return {"code": 401, "message": "无效的token"}

        session = await create_conversation_session(int(user_id_str), db)
        await db.commit()
        return ResponseWrapper(code=200, message="创建新对话成功", data=session).model_dump()
    except Exception as e:
        await db.rollback()
        status = getattr(e, "status_code", 500)
        return {"code": status, "message": str(e)}


@router.put("/api/v1/users/conversations/{conversationId}/archive")
async def archive_session(
    conversationId: str = Path(...),
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """Archive session. Mirrors ConversationSessionController.archiveSession."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        if not extract_username(token):
            return {"code": 401, "message": "无效的token"}

        await archive_conversation_session(conversationId, db)
        await db.commit()
        return {"code": 200, "message": "归档成功"}
    except Exception as e:
        await db.rollback()
        return {"code": getattr(e, "status_code", 500), "message": str(e)}


@router.put("/api/v1/users/conversations/{conversationId}/switch")
async def switch_session(
    conversationId: str = Path(...),
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """Switch current session. Mirrors ConversationSessionController.switchSession."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        user_id_str = extract_user_id(token)
        if not user_id_str:
            return {"code": 401, "message": "无效的token"}

        await switch_current_conversation(int(user_id_str), conversationId, db)
        await db.commit()
        return {"code": 200, "message": "切换对话成功"}
    except Exception as e:
        await db.rollback()
        return {"code": getattr(e, "status_code", 500), "message": str(e)}


@router.put("/api/v1/users/conversations/{conversationId}/unarchive")
async def unarchive_session(
    conversationId: str = Path(...),
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """Unarchive session. Mirrors ConversationSessionController.unarchiveSession."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        if not extract_username(token):
            return {"code": 401, "message": "无效的token"}

        await unarchive_conversation_session(conversationId, db)
        await db.commit()
        return {"code": 200, "message": "取消归档成功"}
    except Exception as e:
        await db.rollback()
        return {"code": getattr(e, "status_code", 500), "message": str(e)}
