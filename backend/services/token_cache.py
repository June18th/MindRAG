"""Token cache service - mirrors Java TokenCacheService using Redis."""
import logging

from core.redis import get_redis

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "jwt:valid:"
USER_TOKENS_PREFIX = "jwt:user:"
REFRESH_PREFIX = "jwt:refresh:"
BLACKLIST_PREFIX = "jwt:blacklist:"


async def cache_token(token_id: str, user_id: str, username: str, expire_time_ms: int) -> None:
    """Cache valid access token info in Redis."""
    try:
        r = await get_redis()
        ttl_seconds = int((expire_time_ms / 1000) - (__import__("time").time())) + 300
        if ttl_seconds <= 0:
            return
        key = f"{TOKEN_PREFIX}{token_id}"
        await r.hset(key, mapping={"userId": user_id, "username": username, "expireTime": str(expire_time_ms)})
        await r.expire(key, ttl_seconds)
        await _add_token_to_user(r, user_id, token_id, ttl_seconds)
        logger.debug("Token cached: %s for user: %s", token_id, username)
    except Exception as e:
        logger.error("Failed to cache token: %s - %s", token_id, e)


async def cache_refresh_token(refresh_token_id: str, user_id: str, expire_time_ms: int) -> None:
    """Cache refresh token in Redis."""
    try:
        r = await get_redis()
        ttl_seconds = int((expire_time_ms / 1000) - (__import__("time").time()))
        if ttl_seconds <= 0:
            return
        key = f"{REFRESH_PREFIX}{refresh_token_id}"
        await r.hset(key, mapping={"userId": user_id, "expireTime": str(expire_time_ms)})
        await r.expire(key, ttl_seconds)
        logger.debug("Refresh token cached: %s for user: %s", refresh_token_id, user_id)
    except Exception as e:
        logger.error("Failed to cache refresh token: %s - %s", refresh_token_id, e)


async def is_token_valid(token_id: str) -> bool:
    """Check if token is valid (exists in cache and not blacklisted)."""
    try:
        r = await get_redis()
        if await r.exists(f"{BLACKLIST_PREFIX}{token_id}"):
            return False
        return bool(await r.exists(f"{TOKEN_PREFIX}{token_id}"))
    except Exception as e:
        logger.error("Failed to check token validity: %s - %s", token_id, e)
        return False


async def is_refresh_token_valid(refresh_token_id: str) -> bool:
    """Check if refresh token exists in Redis cache."""
    try:
        r = await get_redis()
        return bool(await r.exists(f"{REFRESH_PREFIX}{refresh_token_id}"))
    except Exception as e:
        logger.error("Failed to check refresh token: %s - %s", refresh_token_id, e)
        return False


async def blacklist_token(token_id: str, expire_time_ms: int) -> None:
    """Add token to blacklist with TTL matching remaining lifetime."""
    try:
        r = await get_redis()
        import time
        ttl_seconds = max(int((expire_time_ms / 1000) - time.time()), 1)
        if ttl_seconds > 0:
            await r.setex(f"{BLACKLIST_PREFIX}{token_id}", ttl_seconds, str(int(time.time() * 1000)))
            logger.debug("Token blacklisted: %s", token_id)
    except Exception as e:
        logger.error("Failed to blacklist token: %s - %s", token_id, e)


async def remove_token(token_id: str, user_id: str | None = None) -> None:
    """Remove token from cache."""
    try:
        r = await get_redis()
        await r.delete(f"{TOKEN_PREFIX}{token_id}")
        if user_id:
            await r.srem(f"{USER_TOKENS_PREFIX}{user_id}:tokens", token_id)
        logger.debug("Token removed from cache: %s", token_id)
    except Exception as e:
        logger.error("Failed to remove token: %s - %s", token_id, e)


async def remove_all_user_tokens(user_id: str) -> None:
    """Invalidate all tokens for a user."""
    try:
        r = await get_redis()
        user_key = f"{USER_TOKENS_PREFIX}{user_id}:tokens"
        token_ids = await r.smembers(user_key)
        for tid in token_ids:
            await r.delete(f"{TOKEN_PREFIX}{tid}")
            await r.delete(f"{BLACKLIST_PREFIX}{tid}")
        await r.delete(user_key)
        logger.info("All tokens removed for user: %s", user_id)
    except Exception as e:
        logger.error("Failed to remove all user tokens: %s - %s", user_id, e)


async def _add_token_to_user(r, user_id: str, token_id: str, ttl_seconds: int) -> None:
    key = f"{USER_TOKENS_PREFIX}{user_id}:tokens"
    await r.sadd(key, token_id)
    await r.expire(key, ttl_seconds)
