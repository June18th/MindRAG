"""JWT utilities matching the Java JwtUtils class exactly."""
import base64
import uuid
from collections.abc import Callable
from datetime import datetime, timezone, timedelta
from typing import Optional

import jwt
from jwt import ExpiredSignatureError

from core.config import settings

ALGORITHM = "HS256"
ACCESS_EXPIRATION_SECONDS = 3600        # 1 hour
REFRESH_EXPIRATION_SECONDS = 604800     # 7 days
REFRESH_THRESHOLD_SECONDS = 300         # 5 min proactive refresh
REFRESH_WINDOW_SECONDS = 600            # 10 min grace period after expiry


def _signing_key() -> bytes:
    return base64.b64decode(settings.jwt_secret_key)


def _generate_token_id() -> str:
    return uuid.uuid4().hex  # 32-char UUID without dashes


def decode_token(token: str) -> dict:
    """Decode and verify JWT. Raises ExpiredSignatureError if expired."""
    return jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])


def decode_token_ignore_expiry(token: str) -> dict | None:
    """Decode JWT, returning claims even if expired."""
    try:
        return jwt.decode(token, _signing_key(), algorithms=[ALGORITHM],
                          options={"verify_exp": False})
    except Exception:
        return None


def extract_token_id(token: str) -> str | None:
    claims = decode_token_ignore_expiry(token)
    return claims.get("tokenId") if claims else None


def extract_username(token: str) -> str | None:
    claims = decode_token_ignore_expiry(token)
    return claims.get("sub") if claims else None


def extract_user_id(token: str) -> str | None:
    claims = decode_token_ignore_expiry(token)
    return claims.get("userId") if claims else None


def extract_role(token: str) -> str | None:
    claims = decode_token_ignore_expiry(token)
    return claims.get("role") if claims else None


def extract_org_tags(token: str) -> str | None:
    claims = decode_token_ignore_expiry(token)
    return claims.get("orgTags") if claims else None


def should_refresh_token(token: str) -> bool:
    """Check if token should be proactively refreshed (remaining < 5 min)."""
    claims = decode_token_ignore_expiry(token)
    if not claims:
        return False
    exp = claims.get("exp")
    if not exp:
        return False
    remaining = exp - datetime.now(tz=timezone.utc).timestamp()
    return 0 < remaining < REFRESH_THRESHOLD_SECONDS


def can_refresh_expired_token(token: str) -> bool:
    """Check if expired token is still within the 10-min grace period."""
    claims = decode_token_ignore_expiry(token)
    if not claims:
        return False
    exp = claims.get("exp")
    if not exp:
        return False
    elapsed = datetime.now(tz=timezone.utc).timestamp() - exp
    return 0 < elapsed < REFRESH_WINDOW_SECONDS


def generate_access_token(username: str, user: dict) -> str:
    """Generate access token with claims matching Java JwtUtils.generateToken."""
    token_id = _generate_token_id()
    expire_time = datetime.now(tz=timezone.utc) + timedelta(seconds=ACCESS_EXPIRATION_SECONDS)

    claims = {
        "tokenId": token_id,
        "role": user.get("role", "USER"),
        "userId": str(user.get("id", "")),
    }
    if user.get("org_tags"):
        claims["orgTags"] = user["org_tags"]
    if user.get("primary_org"):
        claims["primaryOrg"] = user["primary_org"]

    return jwt.encode(
        {**claims, "sub": username, "exp": expire_time},
        _signing_key(),
        algorithm=ALGORITHM,
    )


def generate_refresh_token(username: str, user: dict) -> str:
    """Generate refresh token matching Java JwtUtils.generateRefreshToken."""
    refresh_token_id = _generate_token_id()
    expire_time = datetime.now(tz=timezone.utc) + timedelta(seconds=REFRESH_EXPIRATION_SECONDS)

    claims = {
        "refreshTokenId": refresh_token_id,
        "userId": str(user.get("id", "")),
        "type": "refresh",
    }

    return jwt.encode(
        {**claims, "sub": username, "exp": expire_time},
        _signing_key(),
        algorithm=ALGORITHM,
    )


async def validate_token(token: str, check_redis: Optional[Callable] = None) -> bool:
    """Validate JWT token (signature + optional Redis blacklist check)."""
    try:
        token_id = extract_token_id(token)
        if not token_id:
            return False
        if check_redis and not await check_redis(token_id):
            return False
        decode_token(token)
        return True
    except ExpiredSignatureError:
        return False
    except Exception:
        return False


async def validate_refresh_token(refresh_token: str, check_redis: Optional[Callable] = None) -> bool:
    """Validate refresh token (signature + type check + Redis)."""
    try:
        claims = decode_token_ignore_expiry(refresh_token)
        if not claims:
            return False
        if claims.get("type") != "refresh":
            return False
        refresh_token_id = claims.get("refreshTokenId")
        if not refresh_token_id:
            return False
        if check_redis and not await check_redis(refresh_token_id):
            return False
        decode_token(refresh_token)
        return True
    except ExpiredSignatureError:
        return False
    except Exception:
        return False
