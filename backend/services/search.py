"""Hybrid search via LangChain ElasticsearchStore retriever."""
import logging
from typing import Optional

from langchain_elasticsearch import ElasticsearchStore
from langchain_openai import OpenAIEmbeddings

from core.config import settings
from clients.elasticsearch_client import get_es
from clients.embedding_client import embed_query

logger = logging.getLogger(__name__)

_es_store: ElasticsearchStore | None = None


def _get_es_store() -> ElasticsearchStore:
    global _es_store
    if _es_store is None:
        _es_store = ElasticsearchStore(
            es_connection=get_es(),
            index_name="knowledge_base",
            embedding=OpenAIEmbeddings(
                model=settings.embedding_api_model,
                api_key=settings.embedding_api_key,
                base_url=settings.embedding_api_url,
            ),
            vector_query_field="vector",
            query_field="textContent",
            strategy=ElasticsearchStore.ApproxRetrievalStrategy(hybrid=True),
        )
    return _es_store


async def _resolve_user_id(user_id: str) -> tuple[Optional[str], list[str]]:
    """Resolve user DB ID and effective org tags."""
    from core.database import async_session_factory
    from services.auth import get_user_by_id, get_user_by_username

    async with async_session_factory() as db:
        user = None
        try:
            uid = int(user_id)
            user = await get_user_by_id(db, uid)
        except ValueError:
            user = await get_user_by_username(db, user_id)

        if not user:
            return None, []

        tags = user.org_tags.split(",") if user.org_tags else []
        return str(user.id), tags


async def hybrid_search(query: str, user_id: str, top_k: int = 10) -> list[dict]:
    """Hybrid search with permission filtering."""
    try:
        user_db_id, user_tags = await _resolve_user_id(user_id)
        if not user_db_id:
            return []

        # Build permission filter
        perm_filter = [
            {"term": {"userId": user_db_id}},
            {"term": {"isPublic": True}},
        ]
        if user_tags:
            perm_filter.append({"bool": {"should": [{"term": {"orgTag": t}} for t in user_tags]}})
        if len(perm_filter) == 1:
            filter_body = perm_filter[0]
        else:
            filter_body = {"bool": {"should": perm_filter}}

        query_vector = await embed_query(query, user_id)

        es = get_es()
        recall_k = top_k * 30

        body: dict = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [{"match": {"textContent": query}}],
                    "filter": filter_body,
                }
            },
            "rescore": {
                "window_size": recall_k,
                "query": {
                    "query_weight": 0.2,
                    "rescore_query_weight": 1.0,
                    "rescore_query": {"match": {"textContent": {"query": query, "operator": "and"}}},
                },
            },
        }

        if query_vector:
            body["knn"] = {
                "field": "vector",
                "query_vector": query_vector,
                "k": recall_k,
                "num_candidates": recall_k,
            }

        resp = await es.search(index="knowledge_base", body=body)
        hits = resp.get("hits", {}).get("hits", [])

        results = []
        for h in hits:
            src = h.get("_source", {})
            results.append({
                "fileMd5": src.get("fileMd5", ""),
                "chunkId": src.get("chunkId", 0),
                "textContent": src.get("textContent", ""),
                "score": h.get("_score", 0),
                "userId": src.get("userId", ""),
                "orgTag": src.get("orgTag", ""),
                "isPublic": src.get("isPublic", False),
                "pageNumber": src.get("pageNumber"),
                "anchorText": src.get("anchorText"),
                "retrievalMode": "HYBRID" if query_vector else "TEXT_ONLY",
                "matchedChunkText": src.get("textContent", ""),
            })

        # Attach file names
        if results:
            md5s = list({r["fileMd5"] for r in results})
            async with (await __import__("core.database").async_session_factory)() as db:
                from sqlalchemy import select
                from models.file_upload import FileUpload
                res = await db.execute(
                    select(FileUpload.file_md5, FileUpload.file_name).where(FileUpload.file_md5.in_(md5s))
                )
                name_map = {row[0]: row[1] for row in res.all()}
            for r in results:
                r["fileName"] = name_map.get(r["fileMd5"])

        return results
    except Exception as e:
        logger.error("Hybrid search failed: %s", e)
        return []


async def hybrid_search_public(query: str, top_k: int = 10) -> list[dict]:
    """Public search without user permission."""
    try:
        es = get_es()
        body = {
            "size": top_k,
            "query": {"bool": {"must": [{"match": {"textContent": query}}], "filter": {"term": {"isPublic": True}}}},
        }
        resp = await es.search(index="knowledge_base", body=body)
        return [
            {
                "fileMd5": h["_source"].get("fileMd5", ""),
                "chunkId": h["_source"].get("chunkId", 0),
                "textContent": h["_source"].get("textContent", ""),
                "score": h.get("_score", 0),
                "isPublic": True,
                "retrievalMode": "TEXT_ONLY",
            }
            for h in resp.get("hits", {}).get("hits", [])
        ]
    except Exception as e:
        logger.error("Public search failed: %s", e)
        return []
