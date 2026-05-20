"""Auth service - mirrors Java UserService for registration, authentication, org tags."""
import logging
import re
import time
from typing import Optional

import bcrypt as _bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import settings
from core.redis import get_redis
from models.invite_code import InviteCode
from models.organization_tag import OrganizationTag
from models.user import User

logger = logging.getLogger(__name__)

DEFAULT_ORG_TAG = "DEFAULT"
PRIVATE_TAG_PREFIX = "PRIVATE_"
PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{6,18}$")

# Redis keys
USER_ORG_TAGS_KEY = "user:org_tags:"
USER_PRIMARY_ORG_KEY = "user:primary_org:"
USER_EFFECTIVE_TAGS_KEY = "user:effective_org_tags:"

# LLM/Embedding token balance keys
USER_TOKEN_LLM_KEY = "user:token:llm:"
USER_TOKEN_EMBEDDING_KEY = "user:token:embedding:"


def hash_password(raw: str) -> str:
    return _bcrypt.hashpw(raw.encode(), _bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    return _bcrypt.checkpw(raw.encode(), hashed.encode())


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def register_user(
    db: AsyncSession,
    username: str,
    password: str,
    invite_code_str: str | None = None,
    admin_creator: str | None = None,
    admin_role: bool = False,
) -> User:
    """Register a new user. Mirrors UserService.registerUser."""
    # Validate registration policy
    mode = settings.app_auth_registration_mode
    invite_required = settings.app_auth_invite_required or mode == "INVITE_ONLY"

    if mode == "CLOSED" and not admin_creator:
        from core.exceptions import AppException
        raise AppException(403, "REGISTRATION_CLOSED")

    if invite_required and not admin_creator:
        await _consume_invite_code(db, invite_code_str, username)

    # Validate password
    if not PASSWORD_PATTERN.match(password):
        from core.exceptions import AppException
        raise AppException(400, "密码格式不正确，6-18位字符，必须包含字母和数字")

    # Check duplicate
    existing = await get_user_by_username(db, username)
    if existing:
        from core.exceptions import AppException
        raise AppException(400, "Username already exists")

    # Ensure default org tag
    await _ensure_default_org_tag(db)

    user = User(
        username=username,
        password=hash_password(password),
        role="ADMIN" if admin_role else "USER",
    )
    db.add(user)
    await db.flush()

    if not admin_role:
        # Create private org tag
        private_tag_id = f"{PRIVATE_TAG_PREFIX}{username}"
        await _create_private_org_tag(db, private_tag_id, username, user)

        # Assign org tags
        assigned_tags = [DEFAULT_ORG_TAG, private_tag_id]
        user.org_tags = ",".join(assigned_tags)
        user.primary_org = private_tag_id

        # Cache org tags
        await _cache_user_org_tags(username, assigned_tags)
        await _cache_user_primary_org(username, private_tag_id)

    return user


async def authenticate_user(db: AsyncSession, username: str, password: str) -> str:
    """Authenticate and return username. Raises AppException on failure."""
    user = await get_user_by_username(db, username)
    if not user or not verify_password(password, user.password):
        from core.exceptions import AppException
        raise AppException(401, "Invalid username or password")
    return user.username


async def get_user_org_tags(username: str, db: AsyncSession | None = None) -> dict:
    """Get user's org tags info. Mirrors UserService.getUserOrgTags."""
    if db is None:
        from core.exceptions import AppException
        raise AppException(500, "DB session required")

    user = await get_user_by_username(db, username)
    if not user:
        from core.exceptions import AppException
        raise AppException(404, "User not found")

    # Try cache first (stored as Redis LIST matching Java OrgTagCacheService)
    r = await get_redis()
    org_tags = await r.lrange(f"{USER_ORG_TAGS_KEY}{username}", 0, -1)
    if not org_tags:
        org_tags = user.org_tags.split(",") if user.org_tags else []
        await _cache_user_org_tags(username, org_tags)

    primary_org = await r.get(f"{USER_PRIMARY_ORG_KEY}{username}")
    if not primary_org:
        primary_org = user.primary_org
        if primary_org:
            await r.setex(f"{USER_PRIMARY_ORG_KEY}{username}", 86400, primary_org)

    # Build tag details
    org_tag_details = []
    for tag_id in org_tags:
        if not tag_id:
            continue
        result = await db.execute(select(OrganizationTag).where(OrganizationTag.tag_id == tag_id))
        tag = result.scalar_one_or_none()
        if tag:
            org_tag_details.append({
                "tagId": tag.tag_id,
                "name": tag.name,
                "description": tag.description,
                "uploadMaxSizeBytes": tag.upload_max_size_bytes,
                "uploadMaxSizeMb": _to_mb(tag.upload_max_size_bytes),
            })

    return {"orgTags": org_tags, "primaryOrg": primary_org, "orgTagDetails": org_tag_details}


async def set_user_primary_org(db: AsyncSession, username: str, primary_org: str) -> None:
    """Set user's primary org tag."""
    user = await get_user_by_username(db, username)
    if not user:
        from core.exceptions import AppException
        raise AppException(404, "User not found")

    user_tags = set(user.org_tags.split(",")) if user.org_tags else set()
    if primary_org not in user_tags:
        from core.exceptions import AppException
        raise AppException(400, "Organization tag not assigned to user")

    user.primary_org = primary_org
    await _cache_user_primary_org(username, primary_org)


async def get_user_primary_org(user_id: str, db: AsyncSession) -> str:
    """Resolve user primary org."""
    user = await _resolve_user(db, user_id)
    if not user:
        from core.exceptions import AppException
        raise AppException(404, "User not found")

    r = await get_redis()
    cached = await r.get(f"{USER_PRIMARY_ORG_KEY}{user.username}")
    if cached:
        return cached

    primary = user.primary_org
    if not primary:
        tags = user.org_tags.split(",") if user.org_tags else []
        primary = tags[0] if tags else DEFAULT_ORG_TAG
    await r.setex(f"{USER_PRIMARY_ORG_KEY}{user.username}", 86400, primary)
    return primary


async def get_usage_snapshot(user_id: str) -> dict:
    """Get user token usage snapshot."""
    r = await get_redis()
    today = time.strftime("%Y-%m-%d")

    llm_used = int(await r.get(f"quota:llm:{today}:user:{user_id}") or 0)
    embedding_used = int(await r.get(f"quota:embedding:{today}:user:{user_id}") or 0)
    chat_count = int(await r.get(f"quota:chat:{today}:user:{user_id}") or 0)

    return {
        "day": today,
        "chatCount": chat_count,
        "llm": {"used": llm_used, "limit": 300000},
        "embedding": {"used": embedding_used, "limit": 1000000},
    }


# --- Private helpers ---

async def _resolve_user(db: AsyncSession, user_id: str) -> Optional[User]:
    try:
        uid = int(user_id)
        return await get_user_by_id(db, uid)
    except ValueError:
        return await get_user_by_username(db, user_id)


async def _ensure_default_org_tag(db: AsyncSession) -> None:
    result = await db.execute(
        select(OrganizationTag).where(OrganizationTag.tag_id == DEFAULT_ORG_TAG)
    )
    if result.scalar_one_or_none():
        return

    # Find an admin to be creator
    admin_result = await db.execute(select(User).where(User.role == "ADMIN").limit(1))
    creator = admin_result.scalar_one_or_none()
    if not creator:
        from core.exceptions import AppException
        raise AppException(500, "No admin user exists to initialize default organization tag")

    tag = OrganizationTag(
        tag_id=DEFAULT_ORG_TAG,
        name="默认组织",
        description="系统默认组织标签，自动分配给所有新用户",
        created_by=creator.id,
    )
    db.add(tag)
    logger.info("Default organization tag created")


async def _create_private_org_tag(db: AsyncSession, tag_id: str, username: str, owner: User) -> None:
    result = await db.execute(select(OrganizationTag).where(OrganizationTag.tag_id == tag_id))
    if result.scalar_one_or_none():
        return
    tag = OrganizationTag(
        tag_id=tag_id,
        name=f"{username}的私人空间",
        description="用户的私人组织标签，仅用户本人可访问",
        created_by=owner.id,
    )
    db.add(tag)
    logger.info("Private organization tag created for user: %s", username)


async def _consume_invite_code(db: AsyncSession, code: str | None, username: str) -> None:
    if not code:
        from core.exceptions import AppException
        raise AppException(403, "INVITE_CODE_REQUIRED")

    normalized = code.strip().upper()
    result = await db.execute(
        select(InviteCode).where(InviteCode.code == normalized)
    )
    invite = result.scalar_one_or_none()

    if not invite or not invite.enabled:
        from core.exceptions import AppException
        raise AppException(403, "INVITE_CODE_INVALID")
    if invite.expires_at and invite.expires_at < __import__("datetime").datetime.now():
        from core.exceptions import AppException
        raise AppException(403, "INVITE_CODE_EXPIRED")
    if invite.used_count >= invite.max_uses:
        from core.exceptions import AppException
        raise AppException(403, "INVITE_CODE_EXHAUSTED")

    invite.used_count += 1


async def _cache_user_org_tags(username: str, org_tags: list[str]) -> None:
    r = await get_redis()
    key = f"{USER_ORG_TAGS_KEY}{username}"
    await r.delete(key)
    if org_tags:
        await r.rpush(key, *org_tags)
        await r.expire(key, 86400)


async def _cache_user_primary_org(username: str, primary_org: str) -> None:
    r = await get_redis()
    await r.setex(f"{USER_PRIMARY_ORG_KEY}{username}", 86400, primary_org)


def _to_mb(bytes_val: int | None) -> int | None:
    if bytes_val and bytes_val > 0:
        return bytes_val // (1024 * 1024)
    return None
