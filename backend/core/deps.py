"""FastAPI dependency injection functions."""
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.redis import get_redis
from core.security import (
    can_refresh_expired_token,
    extract_token_id,
    extract_user_id,
    extract_username,
    should_refresh_token,
    validate_token,
)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _check_token_in_redis(token_id: str) -> bool:
    """Check if token is valid in Redis cache (not blacklisted, exists)."""
    r = await get_redis()
    blacklisted = await r.exists(f"jwt:blacklist:{token_id}")
    if blacklisted:
        return False
    valid = await r.exists(f"jwt:valid:{token_id}")
    return bool(valid)


async def get_current_user_id(authorization: str = Header(...)) -> str:
    """Extract userId from Bearer JWT token."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Missing token"})

    if not await validate_token(token, _check_token_in_redis):
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Invalid or expired token"})

    user_id = extract_user_id(token)
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Invalid token claims"})

    return user_id


async def get_current_user(authorization: str = Header(...)) -> dict:
    """Extract full user info from JWT token."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Missing token"})

    if not await validate_token(token, _check_token_in_redis):
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Invalid or expired token"})

    from core.security import decode_token_ignore_expiry
    claims = decode_token_ignore_expiry(token) or {}
    return {
        "user_id": claims.get("userId"),
        "username": claims.get("sub"),
        "role": claims.get("role"),
        "org_tags": claims.get("orgTags", ""),
        "primary_org": claims.get("primaryOrg", ""),
    }


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """Require ADMIN role."""
    if user.get("role") != "ADMIN":
        raise HTTPException(status_code=403, detail={"code": 403, "message": "Admin access required"})
    return user


async def jwt_middleware(request: Request, call_next):
    """Middleware: proactive JWT refresh via New-Token response header."""
    response = await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return response

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        return response

    # Proactive refresh: if remaining time < 5 min, issue new token
    if should_refresh_token(token) or can_refresh_expired_token(token):
        from core.security import generate_access_token
        username = extract_username(token)
        if username:
            # We'd need full user info from DB here, just set the header hint
            response.headers["X-Token-Refresh-Needed"] = "true"

    return response
