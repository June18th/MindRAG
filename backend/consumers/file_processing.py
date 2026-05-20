"""Kafka consumer - file processing pipeline: parse -> embed -> ES index."""
import asyncio
import json
import logging
from io import BytesIO

import httpx
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from core.config import settings
from core.database import async_session_factory
from core.redis import get_redis
from clients.elasticsearch_client import get_es
from clients.embedding_client import embed
from clients.minio_client import get_minio
from models.file_upload import FileUpload
from sqlalchemy import select

logger = logging.getLogger(__name__)

TOPIC = "file-processing-topic1"
DLT_TOPIC = "file-processing-dlt"
GROUP_ID = "file-processing-group"


async def run_consumer():
    """Start the Kafka consumer loop. Mirrors FileProcessingConsumer."""
    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=settings.spring_kafka_bootstrap_servers,
        group_id=GROUP_ID,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="earliest",
    )
    await consumer.start()
    logger.info("Kafka consumer started, listening on topic: %s", TOPIC)

    try:
        async for msg in consumer:
            task = msg.value
            logger.info("Received task: fileMd5=%s taskType=%s", task.get("fileMd5"), task.get("taskType"))
            try:
                await process_task(task)
            except Exception as e:
                logger.exception("Task failed: fileMd5=%s", task.get("fileMd5"))
                await mark_failed(task, str(e))
                await send_to_dlt(task, str(e))
    finally:
        await consumer.stop()


async def process_task(task: dict):
    file_md5 = task["fileMd5"]
    file_path = task.get("filePath", "")
    file_name = task.get("fileName", "")
    user_id = task["userId"]
    org_tag = task.get("orgTag", "")
    is_public = task.get("isPublic", False)
    task_type = task.get("taskType", "UPLOAD_PROCESS")

    await mark_processing(file_md5)

    if task_type == "REINDEX":
        await process_reindex(file_md5, task.get("requesterId", user_id))
        return

    # Download file from MinIO presigned URL or local path
    file_stream = await download_file(file_path)
    if not file_stream:
        raise RuntimeError(f"Failed to download file: {file_path}")

    try:
        # Parse: extract text, split into chunks
        chunks = await parse_document(file_md5, file_stream, file_name, user_id, org_tag, is_public)
        logger.info("Parsed %d chunks for fileMd5=%s", len(chunks), file_md5)

        # Embed + index to ES
        embed_count = await vectorize_and_index(chunks, user_id, org_tag, is_public)
        await mark_completed(file_md5, embed_count)
        logger.info("Vectorization complete: fileMd5=%s, chunks=%d", file_md5, embed_count)
    finally:
        if hasattr(file_stream, "close"):
            file_stream.close()


async def download_file(file_path: str) -> BytesIO | None:
    """Download file from MinIO presigned URL or local path."""
    logger.info("Downloading: %s", file_path)
    try:
        if file_path.startswith("http://") or file_path.startswith("https://"):
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.get(file_path)
                resp.raise_for_status()
                return BytesIO(resp.content)
        else:
            # Local/MinIO object path - download from MinIO
            minio = get_minio()
            obj_path = file_path.replace(f"{settings.minio_endpoint}/", "").replace("uploads/", "")
            if obj_path.startswith("merged/"):
                data = minio.get_object("uploads", obj_path)
                return BytesIO(data.read())
            # Try as local file
            import os
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    return BytesIO(f.read())
    except Exception as e:
        logger.error("Download failed: %s - %s", file_path, e)
    return None


async def parse_document(file_md5: str, stream: BytesIO, file_name: str,
                         user_id: str, org_tag: str, is_public: bool) -> list[dict]:
    """Parse document into text chunks. Simplified - uses basic text extraction."""
    chunks = []
    try:
        content = stream.read().decode("utf-8", errors="replace")
    except Exception:
        return chunks

    # Simple chunking: 512 chars with 100 char overlap
    chunk_size = 512
    overlap = 100
    pos = 0
    idx = 0
    while pos < len(content):
        chunk_text = content[pos:pos + chunk_size]
        chunks.append({
            "file_md5": file_md5, "chunk_id": idx,
            "text_content": chunk_text, "page_number": None,
            "anchor_text": file_name, "model_version": settings.embedding_api_model,
            "user_id": user_id, "org_tag": org_tag, "is_public": is_public,
        })
        idx += 1
        pos += chunk_size - overlap
    return chunks


async def vectorize_and_index(chunks: list[dict], user_id: str,
                               org_tag: str, is_public: bool) -> int:
    """Embed chunk texts and bulk index to ES."""
    if not chunks:
        return 0

    texts = [c["text_content"] for c in chunks]
    vectors = await embed(texts, user_id)
    if not vectors:
        return 0

    es = get_es()
    bulk_body = []
    for i, chunk in enumerate(chunks):
        if i >= len(vectors):
            break
        bulk_body.append({"index": {"_index": "knowledge_base", "_id": f"{chunk['file_md5']}_{chunk['chunk_id']}"}})
        bulk_body.append({
            "fileMd5": chunk["file_md5"], "chunkId": chunk["chunk_id"],
            "textContent": chunk["text_content"], "pageNumber": chunk["page_number"],
            "anchorText": chunk["anchor_text"], "vector": vectors[i],
            "modelVersion": chunk["model_version"], "userId": chunk["user_id"],
            "orgTag": chunk["org_tag"], "isPublic": chunk["is_public"],
        })

    if bulk_body:
        await es.bulk(body=bulk_body, index="knowledge_base")

    async with async_session_factory() as db:
        actual_tokens = sum(len(v) for v in vectors) * len(vectors)
        result = await db.execute(
            select(FileUpload).where(FileUpload.file_md5 == chunk["file_md5"])
        )
        f = result.scalar_one_or_none()
        if f:
            f.actual_embedding_tokens = actual_tokens
            f.actual_chunk_count = len(chunks)
            await db.commit()

    return len(chunks)


async def process_reindex(file_md5: str, requester_id: str):
    """Reindex document - delete old ES docs, re-parse, re-vectorize."""
    es = get_es()
    try:
        await es.delete_by_query(index="knowledge_base",
                                  body={"query": {"term": {"fileMd5": file_md5}}})
    except Exception:
        pass

    # Get file info from DB
    async with async_session_factory() as db:
        result = await db.execute(
            select(FileUpload).where(FileUpload.file_md5 == file_md5)
        )
        f = result.scalar_one_or_none()
        if not f:
            raise RuntimeError(f"File not found: {file_md5}")

        from services.upload import get_merged_file_stream
        stream_data = get_merged_file_stream(file_md5)
        stream = BytesIO(stream_data.read())
        stream_data.close()

        chunks = await parse_document(file_md5, stream, f.file_name,
                                       f.user_id, f.org_tag or "", f.is_public)
        await vectorize_and_index(chunks, f.user_id, f.org_tag or "", f.is_public)


async def mark_processing(file_md5: str):
    async with async_session_factory() as db:
        result = await db.execute(select(FileUpload).where(FileUpload.file_md5 == file_md5))
        f = result.scalar_one_or_none()
        if f:
            f.vectorization_status = FileUpload.VECTORIZATION_PROCESSING
            f.vectorization_error_message = None
            await db.commit()


async def mark_completed(file_md5: str, chunk_count: int):
    async with async_session_factory() as db:
        result = await db.execute(select(FileUpload).where(FileUpload.file_md5 == file_md5))
        f = result.scalar_one_or_none()
        if f:
            f.vectorization_status = FileUpload.VECTORIZATION_COMPLETED
            f.actual_chunk_count = chunk_count
            await db.commit()


async def mark_failed(task: dict, error: str):
    async with async_session_factory() as db:
        result = await db.execute(
            select(FileUpload).where(FileUpload.file_md5 == task["fileMd5"])
        )
        f = result.scalar_one_or_none()
        if f:
            f.vectorization_status = FileUpload.VECTORIZATION_FAILED
            f.vectorization_error_message = error[:1000]
            await db.commit()


async def send_to_dlt(task: dict, error: str):
    """Send failed task to Dead Letter Topic."""
    try:
        producer = AIOKafkaProducer(
            bootstrap_servers=settings.spring_kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await producer.start()
        task["_error"] = error
        await producer.send_and_wait(DLT_TOPIC, task)
        await producer.stop()
    except Exception as e:
        logger.error("Failed to send to DLT: %s", e)
