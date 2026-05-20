"""Embedding client via LangChain OpenAIEmbeddings (DashScope-compatible)."""
import logging
from typing import Optional

from langchain_openai import OpenAIEmbeddings

from core.config import settings

logger = logging.getLogger(__name__)

_embeddings: OpenAIEmbeddings | None = None


def _get_embeddings() -> OpenAIEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = OpenAIEmbeddings(
            model=settings.embedding_api_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_api_url,
            dimensions=settings.embedding_dimension,
        )
    return _embeddings


async def embed(texts: list[str], requester_id: str = "system") -> list[list[float]] | None:
    """Generate embeddings for texts."""
    if not texts:
        return None
    try:
        emb = _get_embeddings()
        vectors = emb.embed_documents(texts)
        return [[float(v) for v in vec] for vec in vectors]
    except Exception as e:
        logger.error("Embedding failed: %s", e)
        return None


async def embed_query(text: str, requester_id: str = "system") -> list[float] | None:
    """Generate embedding for a single query."""
    try:
        emb = _get_embeddings()
        vec = emb.embed_query(text)
        return [float(v) for v in vec]
    except Exception as e:
        logger.error("Query embedding failed: %s", e)
        return None
