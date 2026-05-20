"""Admin API routes - /api/v1/admin/*"""
import hashlib
import logging
import time

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import delete, desc, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.deps import get_admin_user, get_current_user
from core.redis import get_redis
from core.security import extract_username
from models.file_upload import FileUpload
from models.invite_code import InviteCode
from models.organization_tag import OrganizationTag
from models.user import User as UserModel
from models.user_token_record import UserTokenRecord
from schemas.common import ResponseWrapper
from services.auth import (
    get_user_by_id,
    get_user_by_username,
    hash_password,
    register_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# --- Users ---
@router.get("/users")
async def get_all_users(
    user: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users."""
    result = await db.execute(select(UserModel).order_by(desc(UserModel.created_at)))
    users = result.scalars().all()
    data = [
        {"id": u.id, "username": u.username, "role": u.role,
         "orgTags": u.org_tags.split(",") if u.org_tags else [],
         "primaryOrg": u.primary_org, "createdAt": str(u.created_at)}
        for u in users
    ]
    return ResponseWrapper(code=200, message="Get all users successful", data=data).model_dump()


@router.get("/users/list")
async def get_users_list(
    user: dict = Depends(get_admin_user),
    keyword: str | None = Query(None),
    orgTag: str | None = Query(None),
    status: int | None = Query(None),
    page: int = Query(1),
    size: int = Query(10),
    db: AsyncSession = Depends(get_db),
):
    """Paginated user list with filters."""
    result = await db.execute(select(UserModel).order_by(desc(UserModel.created_at)))
    users = result.scalars().all()

    # Apply filters
    filtered = []
    for u in users:
        if orgTag and (not u.org_tags or orgTag not in u.org_tags.split(",")):
            continue
        if keyword and keyword not in u.username:
            continue
        if status is not None:
            expected_role = {1: "USER", 2: "TEST"}.get(status, "ADMIN")
            if u.role != expected_role:
                continue
        filtered.append(u)

    total = len(filtered)
    start = (page - 1) * size
    page_users = filtered[start:start + size]

    data = {
        "content": [
            {"userId": u.id, "username": u.username, "status": 2 if u.role == "TEST" else (1 if u.role == "USER" else 0),
             "createdAt": str(u.created_at), "primaryOrg": u.primary_org}
            for u in page_users
        ],
        "totalElements": total,
        "totalPages": max(1, (total + size - 1) // size),
        "size": size, "number": page,
    }
    return ResponseWrapper(code=200, message="success", data=data).model_dump()


@router.post("/users/create-admin")
async def create_admin(
    request: Request,
    user: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Create admin user."""
    body = await request.json()
    username = body.get("username")
    password = body.get("password")
    if not username or not password:
        return {"code": 400, "message": "Username and password are required"}
    await register_user(db, username, password, admin_creator=user["username"], admin_role=True)
    await db.commit()
    return {"code": 200, "message": "Admin user created successfully"}


@router.put("/users/{user_id}/org-tags")
async def assign_org_tags(
    user_id: int,
    request: Request,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Assign org tags to user."""
    body = await request.json()
    tags = body.get("orgTags", [])
    target = await get_user_by_id(db, user_id)
    if not target:
        return {"code": 404, "message": "User not found"}
    target.org_tags = ",".join(tags)
    await db.flush()
    return {"code": 200, "message": "Organization tags updated"}


@router.post("/users/{user_id}/tokens/add")
async def add_user_tokens(
    user_id: int,
    request: Request,
    admin: dict = Depends(get_admin_user),
):
    """Admin add tokens to user."""
    body = await request.json()
    amount = body.get("amount", 0)
    token_type = body.get("tokenType", "llm")
    reason = body.get("reason", "Admin grant")
    r = await get_redis()
    key = f"user:token:{token_type}:{user_id}"
    await r.incrby(key, amount)
    return {"code": 200, "message": f"Added {amount} {token_type} tokens"}


# --- Invite Codes ---
@router.get("/invite-codes")
async def list_invite_codes(
    admin: dict = Depends(get_admin_user),
    enabled: str | None = Query(None),
    page: int = Query(1),
    size: int = Query(10),
    db: AsyncSession = Depends(get_db),
):
    """List invite codes."""
    q = select(InviteCode).order_by(desc(InviteCode.created_at))
    if enabled and enabled.strip():
        q = q.where(InviteCode.enabled == (enabled.lower() == "true"))
    result = await db.execute(q.offset((page - 1) * size).limit(size))
    count_result = await db.execute(select(func.count()).select_from(InviteCode))
    total = count_result.scalar() or 0
    codes = result.scalars().all()
    return ResponseWrapper(code=200, message="success", data={
        "records": [{"id": c.id, "code": c.code, "maxUses": c.max_uses, "usedCount": c.used_count,
                      "enabled": c.enabled, "expiresAt": str(c.expires_at) if c.expires_at else None} for c in codes],
        "total": total, "pages": max(1, (total + size - 1) // size), "current": page, "size": size,
    }).model_dump()


@router.post("/invite-codes")
async def create_invite_code(
    request: Request,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Create invite code."""
    body = await request.json()
    code = body.get("code") or hashlib.md5(str(time.time()).encode()).hexdigest()[:16].upper()
    invite = InviteCode(code=code, max_uses=body.get("maxUses", 1), used_count=0, enabled=True, created_by=int(admin["user_id"]))
    db.add(invite)
    await db.flush()
    return {"code": 200, "message": "Invite code created", "data": {"id": invite.id, "code": invite.code}}


@router.delete("/invite-codes/{code_id}")
async def delete_invite_code(
    code_id: int,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(InviteCode).where(InviteCode.id == code_id))
    code = result.scalar_one_or_none()
    if not code:
        return {"code": 404, "message": "Not found"}
    await db.delete(code)
    await db.flush()
    return {"code": 200, "message": "Deleted"}


@router.put("/invite-codes/{code_id}")
async def update_invite_code(
    code_id: int,
    request: Request,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(InviteCode).where(InviteCode.id == code_id))
    code = result.scalar_one_or_none()
    if not code:
        return {"code": 404, "message": "Not found"}
    body = await request.json()
    if "enabled" in body:
        code.enabled = body["enabled"]
    if "maxUses" in body:
        code.max_uses = body["maxUses"]
    return {"code": 200, "message": "Updated"}


# --- Organization Tags ---
@router.get("/org-tags")
async def get_org_tags(admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OrganizationTag).order_by(OrganizationTag.created_at))
    tags = result.scalars().all()
    return ResponseWrapper(code=200, message="success", data=[
        {"tagId": t.tag_id, "name": t.name, "description": t.description,
         "parentTag": t.parent_tag, "uploadMaxSizeBytes": t.upload_max_size_bytes}
        for t in tags
    ]).model_dump()


@router.get("/org-tags/tree")
async def get_org_tag_tree(admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OrganizationTag).order_by(OrganizationTag.created_at))
    tags = result.scalars().all()
    tag_map = {t.tag_id: {"tagId": t.tag_id, "name": t.name, "description": t.description,
                           "parentTag": t.parent_tag} for t in tags}
    roots = []
    for t in tags:
        node = tag_map[t.tag_id]
        if not t.parent_tag or t.parent_tag not in tag_map:
            roots.append(node)
        else:
            parent = tag_map[t.parent_tag]
            parent.setdefault("children", []).append(node)
    return ResponseWrapper(code=200, message="success", data=roots).model_dump()


@router.post("/org-tags")
async def create_org_tag(request: Request, admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    body = await request.json()
    tag = OrganizationTag(
        tag_id=body.get("tagId") or f"ORG_{body['name'].replace(' ', '_').upper()}",
        name=body["name"], description=body.get("description", ""),
        parent_tag=body.get("parentTag"), created_by=int(admin["user_id"]),
    )
    db.add(tag)
    await db.flush()
    return {"code": 200, "message": "Created", "data": {"tagId": tag.tag_id}}


@router.delete("/org-tags/{tag_id}")
async def delete_org_tag(tag_id: str, admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OrganizationTag).where(OrganizationTag.tag_id == tag_id))
    tag = result.scalar_one_or_none()
    if not tag:
        return {"code": 404, "message": "Not found"}
    await db.delete(tag)
    await db.flush()
    return {"code": 200, "message": "Deleted"}


# --- System / Usage ---
@router.get("/system/status")
async def system_status(admin: dict = Depends(get_admin_user)):
    r = await get_redis()
    return {"code": 200, "message": "success", "data": {
        "redis": await r.ping(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }}


@router.get("/usage/overview")
async def usage_overview(admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    today_str = time.strftime("%Y-%m-%d")
    user_count = (await db.execute(select(func.count()).select_from(UserModel))).scalar() or 0
    return ResponseWrapper(code=200, message="success", data={
        "days": 7,
        "today": {"date": today_str, "chatRequestCount": 0, "llmUsedTokens": 0, "llmRequestCount": 0, "embeddingUsedTokens": 0, "embeddingRequestCount": 0},
        "trends": [],
        "llmRankings": [],
        "embeddingRankings": [],
        "alerts": [],
        "totalUsers": user_count,
    }).model_dump()


# --- Rate Limits ---
@router.get("/rate-limits")
async def get_rate_limits(admin: dict = Depends(get_admin_user)):
    return ResponseWrapper(code=200, message="获取限流配置成功", data={
        "chatMessage": {"max": 30, "windowSeconds": 60},
        "llmGlobalToken": {"minuteMax": 20, "minuteWindowSeconds": 60, "dayMax": 500, "dayWindowSeconds": 86400},
        "embeddingUploadToken": {"minuteMax": 60, "minuteWindowSeconds": 60, "dayMax": 2000, "dayWindowSeconds": 86400},
        "embeddingQueryRequest": {"minuteMax": 60, "minuteWindowSeconds": 60, "dayMax": 5000, "dayWindowSeconds": 86400},
        "embeddingQueryGlobalToken": {"minuteMax": 100, "minuteWindowSeconds": 60, "dayMax": 5000, "dayWindowSeconds": 86400},
    }).model_dump()


@router.put("/rate-limits")
async def update_rate_limits(request: Request, admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    from models.rate_limit_config import RateLimitConfig
    body = await request.json()
    for item in body if isinstance(body, list) else [body]:
        key = item.get("configKey")
        result = await db.execute(select(RateLimitConfig).where(RateLimitConfig.config_key == key))
        cfg = result.scalar_one_or_none()
        if cfg:
            for attr in ("single_max", "single_window_seconds", "minute_max", "minute_window_seconds",
                         "day_max", "day_window_seconds"):
                if attr in item:
                    setattr(cfg, attr, item[attr])
    await db.flush()
    return {"code": 200, "message": "Rate limits updated"}


# --- Recharge Packages ---
@router.get("/recharge-packages")
async def list_recharge_packages(admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    from models.recharge_package import RechargePackage
    result = await db.execute(select(RechargePackage).where(RechargePackage.deleted == False).order_by(RechargePackage.sort_order))
    pkgs = result.scalars().all()
    return ResponseWrapper(code=200, message="success", data=[
        {"id": p.id, "packageName": p.package_name, "packagePrice": p.package_price,
         "llmToken": p.llm_token, "embeddingToken": p.embedding_token, "enabled": p.enabled}
        for p in pkgs
    ]).model_dump()


@router.post("/recharge-packages")
async def create_recharge_package(request: Request, admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    from models.recharge_package import RechargePackage
    body = await request.json()
    pkg = RechargePackage(
        package_name=body["packageName"], package_price=body.get("packagePrice", 0),
        package_desc=body.get("packageDesc", ""), package_benefit=body.get("packageBenefit", ""),
        llm_token=body.get("llmToken", 0), embedding_token=body.get("embeddingToken", 0),
        sort_order=body.get("sortOrder", 10), enabled=True,
    )
    db.add(pkg)
    await db.flush()
    return {"code": 200, "message": "Created", "data": {"id": pkg.id}}


@router.put("/recharge-packages/{pkg_id}")
async def update_recharge_package(pkg_id: int, request: Request, admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    from models.recharge_package import RechargePackage
    result = await db.execute(select(RechargePackage).where(RechargePackage.id == pkg_id))
    pkg = result.scalar_one_or_none()
    if not pkg:
        return {"code": 404, "message": "Not found"}
    body = await request.json()
    for attr in ("package_name", "package_price", "llm_token", "embedding_token", "sort_order", "package_desc", "package_benefit"):
        key = "".join(w.capitalize() if i else w for i, w in enumerate(attr.split("_")))
        if key in body:
            setattr(pkg, attr, body[key])
    if "enabled" in body:
        pkg.enabled = body["enabled"]
    await db.flush()
    return {"code": 200, "message": "Updated"}


@router.delete("/recharge-packages/{pkg_id}")
async def delete_recharge_package(pkg_id: int, admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    from models.recharge_package import RechargePackage
    result = await db.execute(select(RechargePackage).where(RechargePackage.id == pkg_id))
    pkg = result.scalar_one_or_none()
    if not pkg:
        return {"code": 404, "message": "Not found"}
    pkg.deleted = True
    await db.flush()
    return {"code": 200, "message": "Deleted"}


@router.get("/user-activities")
async def user_activities(admin: dict = Depends(get_admin_user)):
    return ResponseWrapper(code=200, message="success", data=[]).model_dump()


# --- Admin conversations ---
@router.get("/conversation")
async def admin_conversations(
    admin: dict = Depends(get_admin_user),
    page: int = Query(1), size: int = Query(20),
    userid: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from models.conversation import Conversation
    stmt = select(Conversation).order_by(desc(Conversation.timestamp))
    if userid is not None:
        stmt = stmt.where(Conversation.user_id == userid)
    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    convs = result.scalars().all()
    user_ids = {c.user_id for c in convs}
    user_map = {}
    if user_ids:
        ur = await db.execute(select(UserModel.id, UserModel.username).where(UserModel.id.in_(user_ids)))
        user_map = {row[0]: row[1] for row in ur.all()}
    messages = []
    for c in convs:
        ts = str(c.timestamp) if c.timestamp else None
        uname = user_map.get(c.user_id, "unknown")
        messages.append({
            "role": "user", "content": c.question, "timestamp": ts,
            "id": c.id, "conversationId": c.conversation_id, "username": uname,
        })
        if c.answer:
            messages.append({
                "role": "assistant", "content": c.answer, "timestamp": ts,
                "id": c.id, "conversationId": c.conversation_id, "username": uname,
            })
    return ResponseWrapper(code=200, message="success", data=messages).model_dump()


# --- Model Providers ---
@router.get("/model-providers")
async def get_model_providers(admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    from models.model_provider_config import ModelProviderConfig
    result = await db.execute(select(ModelProviderConfig).order_by(ModelProviderConfig.config_scope, ModelProviderConfig.provider_code))
    configs = result.scalars().all()

    def build_provider(c):
        return {
            "provider": c.provider_code, "displayName": c.display_name, "apiStyle": c.api_style,
            "apiBaseUrl": c.api_base_url, "model": c.model_name, "dimension": c.embedding_dimension,
            "enabled": c.enabled, "active": c.active,
            "hasApiKey": bool(c.api_key_ciphertext), "maskedApiKey": (c.api_key_ciphertext[:4] + "****") if c.api_key_ciphertext else "",
        }

    llm_providers = [build_provider(c) for c in configs if c.config_scope == "llm"]
    emb_providers = [build_provider(c) for c in configs if c.config_scope == "embedding"]

    return ResponseWrapper(code=200, message="获取模型配置成功", data={
        "llm": {"scope": "llm", "activeProvider": next((p["provider"] for p in llm_providers if p["active"]), llm_providers[0]["provider"] if llm_providers else ""), "providers": llm_providers},
        "embedding": {"scope": "embedding", "activeProvider": next((p["provider"] for p in emb_providers if p["active"]), emb_providers[0]["provider"] if emb_providers else ""), "providers": emb_providers},
    }).model_dump()


@router.put("/model-providers/{scope}")
async def update_model_provider(scope: str, request: Request, admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    from models.model_provider_config import ModelProviderConfig
    body = await request.json()
    provider_code = body.get("providerCode")
    result = await db.execute(
        select(ModelProviderConfig).where(
            ModelProviderConfig.config_scope == scope,
            ModelProviderConfig.provider_code == provider_code,
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg:
        if "active" in body:
            # Deactivate others in same scope
            if body["active"]:
                all_result = await db.execute(
                    select(ModelProviderConfig).where(ModelProviderConfig.config_scope == scope)
                )
                for c in all_result.scalars().all():
                    c.active = False
            cfg.active = body["active"]
        if "apiKey" in body and body["apiKey"]:
            cfg.api_key_ciphertext = body["apiKey"]
        if "apiBaseUrl" in body:
            cfg.api_base_url = body["apiBaseUrl"]
        if "modelName" in body:
            cfg.model_name = body["modelName"]
        cfg.updated_by = admin["username"]
    await db.flush()
    return {"code": 200, "message": "Model provider updated"}


@router.post("/model-providers/{scope}/test")
async def test_model_provider(scope: str, request: Request, admin: dict = Depends(get_admin_user)):
    body = await request.json()
    import httpx
    test_url = body.get("apiBaseUrl", "") + "/chat/completions"
    api_key = body.get("apiKey", "")
    model = body.get("modelName", "")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(test_url, headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }, json={
                "model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10,
            })
        success = resp.status_code == 200
        return {"code": 200, "message": "Test completed", "data": {"success": success, "statusCode": resp.status_code}}
    except Exception as e:
        return {"code": 200, "message": "Test failed", "data": {"success": False, "error": str(e)}}


# --- RAG Evaluation ---
@router.post("/rag-eval")
async def rag_eval_run(request: Request, admin: dict = Depends(get_admin_user)):
    """Run professional RAG evaluation. Body: {"datasetName": "...", "samples": [...], "generateAnswers": true}"""
    body = await request.json()
    dataset_name = body.get("datasetName", f"eval-{time.strftime('%Y%m%d-%H%M%S')}")
    samples = body.get("samples", [])
    if not samples:
        return {"code": 400, "message": "samples is required"}

    from services.eval_rag import run_evaluation
    result = await run_evaluation(dataset_name, samples, body.get("topK", 5), body.get("generateAnswers", True))

    return ResponseWrapper(code=200, message="评估完成", data={
        "runId": result.run_id,
        "datasetName": result.dataset_name,
        "totalQueries": result.total_queries,
        "metrics": {
            "contextPrecision": result.context_precision,
            "contextRecall": result.context_recall,
            "faithfulness": result.faithfulness,
            "answerRelevancy": result.answer_relevancy,
            "answerCorrectness": result.answer_correctness,
            "ragasScore": result.ragas_score,
            "avgLatencyMs": result.avg_latency_ms,
        },
        "samples": result.samples,
    }).model_dump()


@router.post("/rag-eval/generate-dataset")
async def rag_eval_generate_dataset(request: Request, admin: dict = Depends(get_admin_user)):
    """Generate synthetic test dataset from documents. Body: {"fileMd5List": [...], "numQuestions": 20}"""
    body = await request.json()
    md5_list = body.get("fileMd5List", [])
    if not md5_list:
        return {"code": 400, "message": "fileMd5List is required"}

    from services.eval_rag import generate_synthetic_dataset
    samples = await generate_synthetic_dataset(md5_list, body.get("numQuestions", 20))

    return ResponseWrapper(code=200, message=f"生成 {len(samples)} 条测试用例", data={
        "samples": samples,
    }).model_dump()


@router.get("/rag-eval/history")
async def rag_eval_history(admin: dict = Depends(get_admin_user), limit: int = 20):
    """Get evaluation run history for trend comparison."""
    from services.eval_rag import get_run_history
    runs = await get_run_history(limit)
    return ResponseWrapper(code=200, message="success", data=runs).model_dump()


# --- Dangerous operations ---
@router.post("/clear-all-data")
async def clear_all_data(admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    """Clear all data - DANGEROUS."""
    from models.document_vector import DocumentVector
    from models.chunk_info import ChunkInfo
    from models.conversation import Conversation
    from models.conversation_session import ConversationSession
    for model in [DocumentVector, ChunkInfo, Conversation, ConversationSession, FileUpload]:
        await db.execute(delete(model))
    await db.flush()
    return {"code": 200, "message": "All data cleared"}
