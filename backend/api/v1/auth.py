"""Auth API routes - /api/v1/auth/*"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import (
    decode_token_ignore_expiry,
    extract_username,
    generate_access_token,
    generate_refresh_token,
    validate_refresh_token,
)
from schemas.auth import RefreshTokenRequest
from schemas.common import ResponseWrapper
from services.auth import get_user_by_username
from services.token_cache import (
    cache_refresh_token,
    cache_token,
    is_refresh_token_valid,
)
from api.v1.users import build_rate_limit_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/refreshToken")
async def refresh_token(request: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    """Refresh access token using refresh token. Mirrors AuthController.refreshToken."""
    if not request.refreshToken:
        return ResponseWrapper(code=400, message="Refresh token cannot be empty").model_dump()

    # Validate refresh token (JWT signature + type check + Redis)
    if not await validate_refresh_token(request.refreshToken, is_refresh_token_valid):
        return ResponseWrapper(code=401, message="Invalid refresh token").model_dump()

    username = extract_username(request.refreshToken)
    if not username:
        return ResponseWrapper(code=401, message="Cannot extract username from refresh token").model_dump()

    user = await get_user_by_username(db, username)
    if not user:
        return ResponseWrapper(code=401, message="User not found").model_dump()

    user_dict = {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "org_tags": user.org_tags,
        "primary_org": user.primary_org,
    }

    new_token = generate_access_token(username, user_dict)
    new_refresh_token = generate_refresh_token(username, user_dict)

    # Cache tokens in Redis
    import time
    from core.security import extract_token_id, decode_token_ignore_expiry, ACCESS_EXPIRATION_SECONDS, REFRESH_EXPIRATION_SECONDS

    token_id = extract_token_id(new_token)
    if token_id:
        expire_ms = int((time.time() + ACCESS_EXPIRATION_SECONDS) * 1000)
        await cache_token(token_id, str(user.id), username, expire_ms)

    claims = decode_token_ignore_expiry(new_refresh_token)
    if claims and claims.get("refreshTokenId"):
        expire_ms = int((time.time() + REFRESH_EXPIRATION_SECONDS) * 1000)
        await cache_refresh_token(claims["refreshTokenId"], str(user.id), expire_ms)

    return ResponseWrapper(
        code=200,
        message="Token refreshed successfully",
        data={"token": new_token, "refreshToken": new_refresh_token},
    ).model_dump()


@router.get("/error")
async def custom_error(code: int, msg: str):
    """Test error endpoint. Mirrors AuthController.customBackendError."""
    return {"code": code, "message": msg}
