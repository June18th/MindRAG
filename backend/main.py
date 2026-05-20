"""KnowHub Python backend - FastAPI application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from api.v1.admin import router as admin_router
from api.v1.auth import router as auth_router
from api.v1.chat import router as chat_router
from api.v1.conversation import router as conversation_router
from api.v1.documents import router as documents_router
from api.v1.search import router as search_router
from api.v1.recharge import router as recharge_router
from api.v1.upload import router as upload_router
from api.v1.users import router as users_router
from websocket.chat_handler import router as ws_router
from core.config import settings
from core.database import async_session_factory
from core.redis import close_redis
from models.user import User
from services.auth import hash_password

logger = logging.getLogger(__name__)

WEAK_PASSWORDS = {"admin123", "admin", "password", "123456", "12345678", "qwerty"}


async def _bootstrap_admin():
    """Create admin user on startup if enabled and not exists. Mirrors AdminUserInitializer."""
    if not settings.admin_bootstrap_enabled:
        logger.info("Admin bootstrap disabled")
        return

    username = settings.admin_bootstrap_username
    password = settings.admin_bootstrap_password

    if not username or not password:
        raise RuntimeError("admin.bootstrap.username/password must be set when bootstrap enabled")
    if len(password) < 12:
        raise RuntimeError("admin.bootstrap.password must be >= 12 characters")
    if password.lower() in WEAK_PASSWORDS:
        raise RuntimeError("admin.bootstrap.password must not be a weak password")

    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none():
            logger.info("Admin user '%s' already exists, skipping", username)
            return

        admin = User(
            username=username,
            password=hash_password(password),
            role="ADMIN",
            primary_org=settings.admin_bootstrap_primary_org,
            org_tags=settings.admin_bootstrap_org_tags,
        )
        db.add(admin)
        await db.commit()
        logger.info("Admin user '%s' created successfully", username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _bootstrap_admin()
    # Start Kafka consumer in background
    consumer_task = asyncio.create_task(_start_kafka_consumer())
    yield
    consumer_task.cancel()
    await close_redis()


async def _start_kafka_consumer():
    try:
        from consumers.file_processing import run_consumer
        await run_consumer()
    except Exception as e:
        logger.warning("Kafka consumer not started: %s", e)


app = FastAPI(
    title="KnowHub API",
    description="智枢 KnowHub - AI Knowledge Management System",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
origins = [o.strip() for o in settings.security_allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(recharge_router)
app.include_router(upload_router)
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(chat_router)
app.include_router(conversation_router)
app.include_router(admin_router)
app.include_router(ws_router)


@app.get("/api/v1/health")
async def health_check():
    return {"code": 200, "message": "Python backend is running", "data": {"status": "healthy"}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
