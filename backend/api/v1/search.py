"""Search API route - /api/v1/search/*"""
import logging

from fastapi import APIRouter, Depends, Header, Query

from core.deps import get_current_user
from core.security import decode_token_ignore_expiry
from schemas.common import ResponseWrapper
from services.search import hybrid_search, hybrid_search_public

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.get("/hybrid")
async def search(
    query: str = Query(...),
    topK: int = Query(10, alias="topK"),
    authorization: str | None = Header(None),
):
    """Hybrid search (KNN+BM25) with permission filtering."""
    try:
        user_id = None
        if authorization and authorization.startswith("Bearer "):
            claims = decode_token_ignore_expiry(authorization[7:])
            if claims:
                user_id = claims.get("userId")

        if user_id:
            results = await hybrid_search(query, user_id, topK)
        else:
            results = await hybrid_search_public(query, topK)

        return ResponseWrapper(code=200, message="success", data=results).model_dump()
    except Exception as e:
        return {"code": 500, "message": str(e), "data": []}
