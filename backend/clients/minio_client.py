"""Async MinIO client wrapper."""
from minio import Minio

from core.config import settings

_minio: Minio | None = None


def get_minio() -> Minio:
    global _minio
    if _minio is None:
        endpoint = settings.minio_endpoint.replace("http://", "").replace("https://", "")
        secure = settings.minio_endpoint.startswith("https://")
        _minio = Minio(
            endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=secure,
        )
    return _minio
