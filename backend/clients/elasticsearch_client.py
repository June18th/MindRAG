"""Async Elasticsearch client wrapper."""
from elasticsearch import AsyncElasticsearch

from core.config import settings

_es: AsyncElasticsearch | None = None


def get_es() -> AsyncElasticsearch:
    global _es
    if _es is None:
        auth = (settings.elasticsearch_username, settings.elasticsearch_password) if settings.elasticsearch_password else None
        _es = AsyncElasticsearch(
            [settings.es_hosts],
            basic_auth=auth,
            verify_certs=not settings.elasticsearch_insecure_trust_all_certificates,
        )
    return _es
