import logging
import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

_pool: aioredis.ConnectionPool | None = None


async def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        password = settings.spring_data_redis_password
        host = settings.spring_data_redis_host
        port = settings.spring_data_redis_port
        logger.info("Creating Redis pool: host=%s port=%s password_set=%s", host, port, bool(password))
        _pool = aioredis.ConnectionPool(
            host=host,
            port=port,
            password=password or None,
            max_connections=50,
            decode_responses=True,
            protocol=2,
        )
    return aioredis.Redis(connection_pool=_pool)


async def close_redis():
    global _pool
    if _pool:
        await _pool.disconnect()
        _pool = None
