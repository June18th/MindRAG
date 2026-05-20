"""WebSocket chat handler - mirrors Java ChatWebSocketHandler."""
import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.security import decode_token_ignore_expiry
from services.chat import (
    INTERNAL_CMD_TOKEN,
    get_active_generation,
    get_generation_state,
    get_internal_cmd_token,
    process_chat_message,
    stop_response,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Active WebSocket sessions: userId -> set[WebSocket]
sessions: dict[str, set[WebSocket]] = {}


async def send_to_user(user_id: str, data: dict) -> None:
    """Send JSON message to all WebSocket sessions of a user."""
    user_sessions = sessions.get(user_id, set())
    dead = set()
    for ws in user_sessions:
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    for ws in dead:
        user_sessions.discard(ws)


@router.websocket("/chat/{token}")
async def chat_websocket(ws: WebSocket, token: str):
    """Main chat WebSocket endpoint. Matches Java ChatWebSocketHandler."""
    await ws.accept()

    # Validate JWT
    claims = decode_token_ignore_expiry(token)
    if not claims:
        await ws.close(code=1008, reason="Invalid token")
        return

    user_id = claims.get("userId")
    if not user_id:
        await ws.close(code=1008, reason="Invalid token")
        return

    # Register session
    if user_id not in sessions:
        sessions[user_id] = set()
    sessions[user_id].add(ws)

    # Send connection notification
    await ws.send_json({
        "type": "connection",
        "sessionId": str(id(ws)),
        "message": "WebSocket连接已建立",
    })

    try:
        while True:
            raw = await ws.receive_text()

            # Heartbeat
            if raw == "__chat_ping__":
                await ws.send_text("__chat_pong__")
                continue

            # JSON stop command?
            try:
                data = json.loads(raw)
                if data.get("type") == "stop" and data.get("_internal_cmd_token") == INTERNAL_CMD_TOKEN:
                    await stop_response(user_id, data.get("generationId"),
                                        lambda d: send_to_user(user_id, d))
                    continue
            except json.JSONDecodeError:
                pass

            # Ordinary chat message
            await process_chat_message(
                user_id, raw,
                send_json=lambda d: send_to_user(user_id, d),
            )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket error for user %s: %s", user_id, e)
        try:
            await ws.send_json({"error": str(e)})
        except Exception:
            pass
    finally:
        sessions[user_id].discard(ws)
        if not sessions[user_id]:
            del sessions[user_id]


@router.get("/chat/{token}")
async def chat_websocket_info(token: str):
    """Info endpoint - WebSocket upgrade happens at this path."""
    return {"message": "WebSocket endpoint. Use ws:// to connect."}
