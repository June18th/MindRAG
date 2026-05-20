"""Chat HTTP API routes - /api/v1/chat/*"""
from fastapi import APIRouter, Depends, Header, Path, Request

from core.deps import DbSession, get_current_user
from schemas.common import ResponseWrapper
from services.chat import (
    get_active_generation,
    get_generation_state,
    get_internal_cmd_token,
)
from services.rate_limiter import check_chat_by_user

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.get("/websocket-token")
async def get_websocket_token(user: dict = Depends(get_current_user)):
    """Get WebSocket stop command token. Mirrors ChatController.getWebSocketToken."""
    cmd_token = get_internal_cmd_token()
    if not cmd_token:
        return ResponseWrapper(code=500, message="Token生成失败").model_dump()
    return ResponseWrapper(
        code=200, message="获取WebSocket停止指令Token成功",
        data={"cmdToken": cmd_token},
    ).model_dump()


@router.get("/generation/{generationId}")
async def get_generation(
    generationId: str = Path(...),
    user: dict = Depends(get_current_user),
):
    """Get generation state. Mirrors ChatController.getGeneration."""
    user_id = user.get("user_id")
    state = await get_generation_state(generationId, user_id)
    if state is None:
        return ResponseWrapper(code=200, message="获取生成状态成功", data=None).model_dump()
    return ResponseWrapper(code=200, message="获取生成状态成功", data=state).model_dump()


@router.get("/active-generation")
async def active_generation(user: dict = Depends(get_current_user)):
    """Get user's active generation. Mirrors ChatController.getActiveGeneration."""
    user_id = user.get("user_id")
    state = await get_active_generation(user_id)
    return ResponseWrapper(
        code=200, message="获取当前活动生成状态成功", data=state,
    ).model_dump()


@router.post("/feedback")
async def submit_feedback(request: Request, user: dict = Depends(get_current_user)):
    """Submit conversation feedback. Mirrors ChatController.submitFeedback."""
    body = await request.json()
    rating = body.get("rating", "")
    if not rating:
        return ResponseWrapper(code=400, message="rating 不能为空").model_dump()

    from services.chat import _execute_feedback
    await _execute_feedback(user.get("user_id"), body)
    return ResponseWrapper(code=200, message="反馈已记录").model_dump()
