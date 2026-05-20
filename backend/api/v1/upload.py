"""Upload API routes - /api/v1/upload/*"""
import logging

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.deps import get_current_user
from models.file_upload import FileUpload
from schemas.common import ResponseWrapper
from services.upload import (
    generate_presigned_url,
    get_total_chunks,
    get_uploaded_chunks,
    merge_chunks,
    upload_chunk as upload_chunk_svc,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])

SUPPORTED_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "txt", "md", "csv", "json", "xml", "html", "htm",
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "svg",
}
SUPPORTED_TYPES = {
    "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain", "text/markdown", "text/csv", "text/html",
    "application/json", "application/xml",
    "image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp", "image/svg+xml",
}


def _get_ext(file_name: str) -> str:
    dot = file_name.rfind(".")
    return file_name[dot + 1:].lower() if dot >= 0 else ""


@router.post("/chunk")
async def upload_chunk(
    fileMd5: str = Form(...),
    chunkIndex: int = Form(...),
    totalSize: int = Form(...),
    fileName: str = Form(...),
    totalChunks: int | None = Form(None),
    orgTag: str | None = Form(None),
    isPublic: bool = Form(False),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file chunk. Mirrors UploadController.uploadChunk."""
    try:
        user_id = user.get("user_id")
        if not user_id:
            return {"code": 401, "message": "Invalid token"}
        if not file.filename:
            return {"code": 400, "message": "File name is required"}

        # Validate file type on first chunk
        if chunkIndex == 0:
            ext = _get_ext(fileName)
            if ext not in SUPPORTED_EXTENSIONS:
                return ResponseWrapper(
                    code=400,
                    message=f"不支持的文件类型：{ext}",
                    data={"fileType": ext, "supportedTypes": list(SUPPORTED_TYPES)},
                ).model_dump()

        # Resolve org tag
        if not orgTag:
            from services.auth import get_user_primary_org
            orgTag = await get_user_primary_org(user_id, db)

        file_data = await file.read()
        await upload_chunk_svc(
            db, fileMd5, chunkIndex, totalSize, fileName,
            file_data, orgTag, isPublic, user_id,
        )
        await db.commit()

        uploaded = await get_uploaded_chunks(fileMd5, user_id)
        total = await get_total_chunks(fileMd5, user_id, db)
        progress = (len(uploaded) / total * 100) if total > 0 else 0

        return ResponseWrapper(
            code=200, message="分片上传成功",
            data={"uploaded": uploaded, "progress": round(progress, 2)},
        ).model_dump()

    except Exception as e:
        await db.rollback()
        return {"code": 500, "message": f"分片上传失败: {e}"}


@router.get("/status")
async def upload_status(
    file_md5: str = Query(..., alias="file_md5"),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get upload status. Mirrors UploadController.getUploadStatus."""
    try:
        user_id = user.get("user_id")
        uploaded = await get_uploaded_chunks(file_md5, user_id)
        total = await get_total_chunks(file_md5, user_id, db)
        progress = (len(uploaded) / total * 100) if total > 0 else 0

        return ResponseWrapper(
            code=200, message="获取上传状态成功",
            data={"uploaded": uploaded, "progress": round(progress, 2)},
        ).model_dump()
    except Exception as e:
        return {"code": 500, "message": f"获取上传状态失败: {e}"}


@router.post("/merge")
async def merge_file(
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Merge uploaded chunks. Mirrors UploadController.mergeFile."""
    try:
        body = await request.json()
        file_md5 = body.get("fileMd5")
        file_name = body.get("fileName")
        user_id = user.get("user_id")

        if not file_md5 or not file_name:
            return {"code": 400, "message": "fileMd5 and fileName are required"}

        # Check file ownership
        from sqlalchemy import select
        result = await db.execute(
            select(FileUpload)
            .where(FileUpload.file_md5 == file_md5, FileUpload.user_id == user_id)
            .order_by(FileUpload.created_at.desc()).limit(1)
        )
        f = result.scalar_one_or_none()
        if not f:
            return {"code": 404, "message": "文件记录不存在"}
        if f.user_id != user_id:
            return {"code": 403, "message": "没有权限操作此文件"}
        if f.status == FileUpload.STATUS_COMPLETED:
            url = generate_presigned_url(file_md5)
            return ResponseWrapper(
                code=200, message="文件已完成合并",
                data={"object_url": url},
            ).model_dump()

        # Check all chunks uploaded
        uploaded = await get_uploaded_chunks(file_md5, user_id)
        total = await get_total_chunks(file_md5, user_id, db)
        if len(uploaded) < total:
            return {"code": 400, "message": "文件分片未全部上传，无法合并"}

        # Mark as merging
        from models.file_upload import FileUpload as FU
        f.status = FU.STATUS_MERGING
        await db.flush()

        # Merge
        try:
            object_url = await merge_chunks(db, file_md5, file_name, user_id)
        except Exception:
            f.status = FU.STATUS_UPLOADING
            await db.flush()
            raise

        await db.commit()

        # Send to Kafka for async processing
        try:
            from aiokafka import AIOKafkaProducer
            from core.config import settings
            producer = AIOKafkaProducer(
                bootstrap_servers=settings.spring_kafka_bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
            import json as _json
            await producer.start()
            await producer.send_and_wait("file-processing-topic1", {
                "fileMd5": file_md5, "filePath": object_url, "fileName": file_name,
                "userId": user_id, "orgTag": org_tag, "isPublic": is_public,
                "taskType": "UPLOAD_PROCESS", "requesterId": user_id,
            })
            await producer.stop()
        except Exception as e:
            logger.warning("Failed to send Kafka message: %s", e)

        return ResponseWrapper(
            code=200, message="文件合并成功，任务已发送到 Kafka",
            data={"object_url": object_url},
        ).model_dump()

    except Exception as e:
        await db.rollback()
        return {"code": 500, "message": f"文件合并失败: {e}"}


@router.get("/supported-types")
async def get_supported_types():
    """Get supported file types. Mirrors UploadController.getSupportedFileTypes."""
    return ResponseWrapper(
        code=200, message="获取支持的文件类型成功",
        data={
            "supportedTypes": list(SUPPORTED_TYPES),
            "supportedExtensions": list(SUPPORTED_EXTENSIONS),
            "description": "系统支持的文档类型文件，这些文件可以被解析并进行向量化处理",
        },
    ).model_dump()
