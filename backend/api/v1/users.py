"""User API routes - /api/v1/users/*"""
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.deps import get_current_user
from core.redis import get_redis
from core.security import (
    ACCESS_EXPIRATION_SECONDS,
    REFRESH_EXPIRATION_SECONDS,
    decode_token,
    decode_token_ignore_expiry,
    extract_token_id,
    extract_user_id,
    extract_username,
    generate_access_token,
    generate_refresh_token,
    validate_token,
)
from models.user import User
from models.user_token_record import UserTokenRecord
from schemas.auth import (
    LoginData,
    OrgTagsData,
    PrimaryOrgRequest,
    TokenRecordItem,
    TokenRecordsPage,
    UploadOrgsData,
    UsageSnapshotData,
    UserLoginRequest,
    UserProfileData,
    UserRegisterRequest,
)
from schemas.common import ResponseWrapper
from services.auth import (
    authenticate_user,
    get_user_by_username,
    get_user_org_tags,
    get_user_primary_org,
    get_usage_snapshot,
    register_user,
    set_user_primary_org,
)
from services.rate_limiter import check_login_by_ip, check_register_by_ip
from services.token_cache import (
    blacklist_token,
    cache_refresh_token,
    cache_token,
    is_token_valid,
    remove_all_user_tokens,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def build_rate_limit_response(retry_after: int, message: str) -> dict:
    return {"code": 429, "message": message, "retryAfterSeconds": retry_after}


def _resolve_client_ip(request: Request) -> str:
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


@router.post("/register")
async def register(req: UserRegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Register a new user. Mirrors UserController.register."""
    try:
        await check_register_by_ip(request)

        if not req.username or not req.password:
            return ResponseWrapper(code=400, message="Username and password cannot be empty").model_dump()

        user = await register_user(db, req.username, req.password, req.inviteCode)
        await db.commit()
        return ResponseWrapper(code=200, message="User registered successfully").model_dump()

    except Exception as e:
        from core.exceptions import RateLimitExceeded
        if isinstance(e, RateLimitExceeded):
            return build_rate_limit_response(e.retry_after_seconds, e.detail["message"])
        await db.rollback()
        status_code = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", {"code": 500, "message": str(e)})
        if isinstance(detail, dict):
            return detail
        return {"code": status_code, "message": str(e)}


@router.post("/login")
async def login(req: UserLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Login and get JWT tokens. Mirrors UserController.login."""
    try:
        await check_login_by_ip(request)

        if not req.username or not req.password:
            return ResponseWrapper(code=400, message="Username and password cannot be empty").model_dump()

        username = await authenticate_user(db, req.username, req.password)
        if not username:
            return {"code": 401, "message": "Invalid credentials"}

        user = await get_user_by_username(db, username)
        if not user:
            return {"code": 401, "message": "Invalid credentials"}

        user_dict = {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "org_tags": user.org_tags,
            "primary_org": user.primary_org,
        }

        token = generate_access_token(username, user_dict)
        refresh_token = generate_refresh_token(username, user_dict)

        # Cache tokens
        import time as _time
        token_id = extract_token_id(token)
        if token_id:
            expire_ms = int((_time.time() + ACCESS_EXPIRATION_SECONDS) * 1000)
            await cache_token(token_id, str(user.id), username, expire_ms)

        refresh_claims = decode_token_ignore_expiry(refresh_token)
        if refresh_claims and refresh_claims.get("refreshTokenId"):
            expire_ms = int((_time.time() + REFRESH_EXPIRATION_SECONDS) * 1000)
            await cache_refresh_token(refresh_claims["refreshTokenId"], str(user.id), expire_ms)

        return ResponseWrapper(
            code=200,
            message="Login successful",
            data={"token": token, "refreshToken": refresh_token},
        ).model_dump()

    except Exception as e:
        from core.exceptions import RateLimitExceeded
        if isinstance(e, RateLimitExceeded):
            return build_rate_limit_response(e.retry_after_seconds, e.detail["message"])
        await db.rollback()
        status_code = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", {"code": 500, "message": str(e)})
        if isinstance(detail, dict):
            return detail
        return {"code": status_code, "message": str(e)}


@router.get("/me")
async def get_current_user_profile(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get current user profile. Mirrors UserController.getCurrentUser."""
    try:
        username = user.get("username")
        if not username:
            return {"code": 401, "message": "Invalid token"}

        db_user = await get_user_by_username(db, username)
        if not db_user:
            return {"code": 404, "message": "User not found"}

        org_tags = db_user.org_tags.split(",") if db_user.org_tags else []
        profile = UserProfileData(
            id=db_user.id,
            username=db_user.username,
            role=db_user.role,
            orgTags=org_tags,
            primaryOrg=db_user.primary_org,
            createdAt=db_user.created_at,
            updatedAt=db_user.updated_at,
        )
        return ResponseWrapper(
            code=200, message="Get user detail successful", data=profile.model_dump()
        ).model_dump()

    except Exception as e:
        status_code = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", {"code": 500, "message": str(e)})
        if isinstance(detail, dict):
            return detail
        return {"code": status_code, "message": str(e)}


@router.get("/org-tags")
async def get_org_tags(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get user's organization tags. Mirrors UserController.getUserOrgTags."""
    try:
        username = user.get("username")
        if not username:
            return {"code": 401, "message": "Invalid token"}

        org_tags_info = await get_user_org_tags(username, db)
        return ResponseWrapper(
            code=200, message="Get user organization tags successful", data=org_tags_info
        ).model_dump()

    except Exception as e:
        status_code = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", {"code": 500, "message": str(e)})
        if isinstance(detail, dict):
            return detail
        return {"code": status_code, "message": str(e)}


@router.put("/primary-org")
async def set_primary_org(
    req: PrimaryOrgRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set user's primary organization tag. Mirrors UserController.setPrimaryOrg."""
    try:
        username = user.get("username")
        if not username:
            return {"code": 401, "message": "Invalid token"}
        if not req.primaryOrg:
            return {"code": 400, "message": "Primary organization tag cannot be empty"}

        await set_user_primary_org(db, username, req.primaryOrg)
        return ResponseWrapper(code=200, message="Primary organization set successfully").model_dump()

    except Exception as e:
        status_code = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", {"code": 500, "message": str(e)})
        if isinstance(detail, dict):
            return detail
        return {"code": status_code, "message": str(e)}


@router.get("/upload-orgs")
async def get_upload_orgs(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get org tags for file upload. Mirrors UserController.getUploadOrgTags."""
    try:
        user_id = user.get("user_id")
        if not user_id:
            return {"code": 401, "message": "Invalid token"}

        db_user = await get_user_by_username(db, user.get("username", ""))
        if not db_user:
            return {"code": 404, "message": "User not found"}

        org_tags_list = db_user.org_tags.split(",") if db_user.org_tags else []
        primary_org = await get_user_primary_org(user_id, db)

        data = UploadOrgsData(orgTags=org_tags_list, primaryOrg=primary_org)
        return ResponseWrapper(
            code=200, message="获取用户上传组织标签成功", data=data.model_dump()
        ).model_dump()

    except Exception as e:
        status_code = getattr(e, "status_code", 500)
        detail = getattr(e, "detail", {"code": 500, "message": str(e)})
        if isinstance(detail, dict):
            return detail
        return {"code": status_code, "message": str(e)}


@router.get("/usage")
async def get_usage(user: dict = Depends(get_current_user)):
    """Get user usage snapshot. Mirrors UserController.getCurrentUserUsage."""
    try:
        user_id = user.get("user_id")
        if not user_id:
            return {"code": 401, "message": "Invalid token"}

        snapshot = await get_usage_snapshot(user_id)
        return ResponseWrapper(code=200, message="Get user usage successful", data=snapshot).model_dump()

    except Exception as e:
        return {"code": 500, "message": f"Failed to get usage: {e}"}


@router.post("/logout")
async def logout(authorization: str = Header(...)):
    """Logout current session. Mirrors UserController.logout."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            return {"code": 400, "message": "Invalid token format"}

        username = extract_username(token)
        if not username:
            return {"code": 401, "message": "Invalid token"}

        token_id = extract_token_id(token)
        if token_id:
            claims = decode_token_ignore_expiry(token)
            if claims and claims.get("exp"):
                expire_ms = int(claims["exp"]) * 1000
                await blacklist_token(token_id, expire_ms)
                user_id = claims.get("userId")
                from services.token_cache import remove_token
                await remove_token(token_id, user_id)

        return {"code": 200, "message": "Logout successful"}

    except Exception as e:
        return {"code": 500, "message": "Internal server error"}


@router.post("/logout-all")
async def logout_all(authorization: str = Header(...)):
    """Logout from all devices. Mirrors UserController.logoutAll."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            return {"code": 400, "message": "Invalid token format"}

        username = extract_username(token)
        user_id = extract_user_id(token)
        if not username or not user_id:
            return {"code": 401, "message": "Invalid token"}

        await remove_all_user_tokens(user_id)
        return {"code": 200, "message": "Logout from all devices successful"}

    except Exception as e:
        return {"code": 500, "message": "Internal server error"}


@router.get("/token-records")
async def get_token_records(
    authorization: str = Header(...),
    page: int = Query(0),
    size: int = Query(10),
    db: AsyncSession = Depends(get_db),
):
    """Get user token records (paginated). Mirrors UserController.getTokenRecords."""
    try:
        token = authorization.removeprefix("Bearer ").strip()
        user_id = extract_user_id(token)
        if not user_id:
            return {"code": 401, "message": "Invalid token"}

        # Count total
        count_query = select(func.count()).select_from(UserTokenRecord).where(
            UserTokenRecord.user_id == user_id
        )
        total = (await db.execute(count_query)).scalar() or 0

        total_pages = max(1, (total + size - 1) // size) if size > 0 else 1

        # Fetch page
        stmt = (
            select(UserTokenRecord)
            .where(UserTokenRecord.user_id == user_id)
            .order_by(desc(UserTokenRecord.created_at))
            .offset(page * size)
            .limit(size)
        )
        result = await db.execute(stmt)
        records = result.scalars().all()

        items = [
            TokenRecordItem(
                id=r.id,
                recordDate=r.record_date,
                tokenType=r.token_type,
                changeType=r.change_type,
                amount=r.amount or 0,
                balanceBefore=r.balance_before,
                balanceAfter=r.balance_after,
                reason=r.reason,
                remark=r.remark,
                createdAt=r.created_at,
                requestCount=r.request_count or 0,
            )
            for r in records
        ]

        page_data = TokenRecordsPage(
            content=items,
            totalElements=total,
            totalPages=total_pages,
            number=page,
            size=size,
            first=page == 0,
            last=page >= total_pages - 1,
            empty=len(items) == 0,
        )

        return ResponseWrapper(
            code=200, message="Get token records successful", data=page_data.model_dump()
        ).model_dump()

    except Exception as e:
        return {"code": 500, "message": "Internal server error"}
