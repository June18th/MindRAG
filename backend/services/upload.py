"""Upload service - mirrors Java UploadService with Redis bitmap + MinIO chunk management."""
import hashlib
import logging
from datetime import datetime, timedelta
from io import BytesIO

import orjson
from minio import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clients.minio_client import get_minio
from core.config import settings
from core.redis import get_redis
from models.chunk_info import ChunkInfo
from models.file_upload import FileUpload

logger = logging.getLogger(__name__)

BUCKET = "uploads"
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB


async def get_or_create_file_upload(
    db: AsyncSession,
    file_md5: str,
    total_size: int,
    file_name: str,
    org_tag: str,
    is_public: bool,
    user_id: str,
) -> FileUpload:
    """Get existing or create new FileUpload record."""
    result = await db.execute(
        select(FileUpload)
        .where(FileUpload.file_md5 == file_md5, FileUpload.user_id == user_id)
        .order_by(FileUpload.created_at.desc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    f = FileUpload(
        file_md5=file_md5,
        file_name=file_name,
        total_size=total_size,
        status=FileUpload.STATUS_UPLOADING,
        user_id=user_id,
        org_tag=org_tag,
        is_public=is_public,
    )
    db.add(f)
    await db.flush()
    return f


def _redis_upload_key(user_id: str, file_md5: str) -> str:
    return f"upload:{user_id}:{file_md5}"


def _chunk_storage_path(file_md5: str, chunk_index: int) -> str:
    return f"chunks/{file_md5}/{chunk_index}"


async def is_chunk_uploaded(file_md5: str, chunk_index: int, user_id: str) -> bool:
    r = await get_redis()
    return bool(await r.getbit(_redis_upload_key(user_id, file_md5), chunk_index))


async def mark_chunk_uploaded(file_md5: str, chunk_index: int, user_id: str) -> None:
    r = await get_redis()
    await r.setbit(_redis_upload_key(user_id, file_md5), chunk_index, 1)


async def get_uploaded_chunks(file_md5: str, user_id: str) -> list[int]:
    r = await get_redis()
    key = _redis_upload_key(user_id, file_md5)
    bitmap = await r.get(key)
    if not bitmap:
        return []
    result = []
    for i in range(len(bitmap) * 8):
        byte_idx = i // 8
        bit_pos = 7 - (i % 8)
        if byte_idx >= len(bitmap):
            break
        if (bitmap[byte_idx] & (1 << bit_pos)) != 0:
            result.append(i)
    return result


async def get_total_chunks(file_md5: str, user_id: str, db: AsyncSession) -> int:
    result = await db.execute(
        select(FileUpload)
        .where(FileUpload.file_md5 == file_md5, FileUpload.user_id == user_id)
        .order_by(FileUpload.created_at.desc())
        .limit(1)
    )
    f = result.scalar_one_or_none()
    if not f:
        return 0
    return max(1, int((f.total_size + CHUNK_SIZE - 1) // CHUNK_SIZE))


async def delete_file_mark(file_md5: str, user_id: str) -> None:
    r = await get_redis()
    await r.delete(_redis_upload_key(user_id, file_md5))


async def save_chunk_info(db: AsyncSession, file_md5: str, chunk_index: int,
                           chunk_md5: str, storage_path: str) -> None:
    try:
        ci = ChunkInfo(file_md5=file_md5, chunk_index=chunk_index,
                       chunk_md5=chunk_md5, storage_path=storage_path)
        db.add(ci)
        await db.flush()
    except Exception:
        pass  # Duplicate chunk, idempotent


async def upload_chunk(
    db: AsyncSession,
    file_md5: str,
    chunk_index: int,
    total_size: int,
    file_name: str,
    file_data: bytes,
    org_tag: str,
    is_public: bool,
    user_id: str,
) -> None:
    """Upload a single chunk to MinIO + track in Redis bitmap + DB."""
    minio = get_minio()
    storage_path = _chunk_storage_path(file_md5, chunk_index)

    # Check idempotency
    if await is_chunk_uploaded(file_md5, chunk_index, user_id):
        result = await db.execute(
            select(ChunkInfo).where(
                ChunkInfo.file_md5 == file_md5, ChunkInfo.chunk_index == chunk_index
            )
        )
        if result.scalar_one_or_none():
            try:
                minio.stat_object(BUCKET, storage_path)
                return  # Already uploaded
            except S3Error:
                pass  # Re-upload

    # Upload to MinIO
    chunk_md5 = hashlib.md5(file_data).hexdigest()
    minio.put_object(BUCKET, storage_path, BytesIO(file_data), len(file_data))

    # Track in DB
    await save_chunk_info(db, file_md5, chunk_index, chunk_md5, storage_path)

    # Mark in Redis bitmap
    await mark_chunk_uploaded(file_md5, chunk_index, user_id)


async def merge_chunks(db: AsyncSession, file_md5: str, file_name: str, user_id: str) -> str:
    """Merge all chunks into a single file in MinIO, return presigned URL."""
    minio = get_minio()
    merged_path = f"merged/{file_md5}"

    # Get all chunks ordered by index
    result = await db.execute(
        select(ChunkInfo)
        .where(ChunkInfo.file_md5 == file_md5)
        .order_by(ChunkInfo.chunk_index.asc())
    )
    chunks = result.scalars().all()

    expected = await get_total_chunks(file_md5, user_id, db)
    if len(chunks) != expected:
        raise RuntimeError(f"Chunk count mismatch: expected {expected}, got {len(chunks)}")

    # Compose (merge) in MinIO
    sources = [f"{BUCKET}/{c.storage_path}" for c in chunks]
    # MinIO compose uses up to 32 sources; split if needed
    import math
    if len(sources) <= 32:
        minio.compose_object(BUCKET, merged_path, sources)
    else:
        # Multi-step compose for large numbers
        temp_sources = []
        for batch_start in range(0, len(sources), 32):
            batch = sources[batch_start:batch_start + 32]
            temp_path = f"chunks/{file_md5}/_compose_{batch_start}"
            minio.compose_object(BUCKET, temp_path, batch)
            temp_sources.append(temp_path)
        try:
            minio.compose_object(BUCKET, merged_path, temp_sources)
        finally:
            for tp in temp_sources:
                try:
                    minio.remove_object(BUCKET, tp)
                except Exception:
                    pass

    # Clean up chunks
    for c in chunks:
        try:
            minio.remove_object(BUCKET, c.storage_path)
        except Exception:
            pass

    # Clean up DB
    from sqlalchemy import delete
    await db.execute(delete(ChunkInfo).where(ChunkInfo.file_md5 == file_md5))

    # Clean Redis
    await delete_file_mark(file_md5, user_id)

    # Update file status
    result = await db.execute(
        select(FileUpload)
        .where(FileUpload.file_md5 == file_md5, FileUpload.user_id == user_id)
        .order_by(FileUpload.created_at.desc())
        .limit(1)
    )
    f = result.scalar_one_or_none()
    if f:
        f.status = FileUpload.STATUS_COMPLETED
        f.merged_at = datetime.now()

    # Generate presigned URL (1 hour expiry)
    url = minio.presigned_get_object(BUCKET, merged_path, expires=timedelta(hours=1))
    return url


def generate_presigned_url(file_md5: str) -> str:
    minio = get_minio()
    return minio.presigned_get_object(BUCKET, f"merged/{file_md5}", expires=timedelta(hours=1))


def get_merged_file_stream(file_md5: str):
    minio = get_minio()
    return minio.get_object(BUCKET, f"merged/{file_md5}")


def remove_merged_file(file_md5: str) -> None:
    minio = get_minio()
    try:
        minio.remove_object(BUCKET, f"merged/{file_md5}")
    except S3Error:
        pass
