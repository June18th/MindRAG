"""Rate limiting service - mirrors Java RateLimitService."""
import logging

from fastapi import Request

from core.config import settings
from core.exceptions import RateLimitExceeded
from core.redis import get_redis

logger = logging.getLogger(__name__)

# Lua script for atomic INCR + EXPIRE (race-condition-free)
INCR_WITH_EXPIRE_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def _resolve_client_ip(request: Request) -> str:
    """Extract client IP, mirroring Java resolveClientIp."""
    for header in [
        "CF-Connecting-IP", "True-Client-IP",
        "X-Forwarded-For", "X-Real-IP",
        "Proxy-Client-IP", "WL-Proxy-Client-IP",
    ]:
        value = request.headers.get(header)
        if not value:
            continue
        if header == "X-Forwarded-For":
            value = value.split(",")[0].strip()
        if value and value.lower() != "unknown":
            return value

    client = request.client
    return client.host if client else "unknown"


async def _check_window(key: str, max_req: int, window_sec: int, error_msg: str) -> None:
    """Fixed window rate limit check via Redis."""
    if max_req <= 0:
        return
    r = await get_redis()
    # Load the Lua script once and cache the SHA
    current = await r.eval(INCR_WITH_EXPIRE_SCRIPT, 1, key, window_sec)
    if int(current) > max_req:
        ttl = await r.ttl(key)
        retry_after = ttl if ttl and ttl > 0 else window_sec
        raise RateLimitExceeded(error_msg, retry_after)


async def check_register_by_ip(request: Request) -> None:
    ip = _resolve_client_ip(request)
    await _check_window(
        f"register:ip:{ip}",
        settings.rate_limit_register_max,
        settings.rate_limit_register_window_seconds,
        "注册请求过于频繁，请稍后再试",
    )


async def check_login_by_ip(request: Request) -> None:
    ip = _resolve_client_ip(request)
    await _check_window(
        f"login:ip:{ip}",
        settings.rate_limit_login_max,
        settings.rate_limit_login_window_seconds,
        "登录请求过于频繁，请稍后再试",
    )


async def check_chat_by_user(user_id: str) -> None:
    await _check_window(
        f"chat:user:{user_id}",
        settings.rate_limit_chat_message_max,
        settings.rate_limit_chat_message_window_seconds,
        "聊天请求过于频繁，请稍后再试",
    )
