"""Document API routes - /api/v1/documents/*"""
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response as FastAPIResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.deps import DbSession, get_current_user
from core.redis import get_redis
from core.security import decode_token_ignore_expiry
from models.file_upload import FileUpload
from schemas.common import ResponseWrapper
from services.document import (
    delete_document,
    generate_download_url,
    get_accessible_files,
    get_file_preview_content,
    get_org_tag_name,
    get_preview_type,
    get_user_uploaded_files,
    _get_extension,
)
from services.upload import generate_presigned_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _resolve_auth(authorization: str | None, fallback_token: str | None) -> tuple[str | None, str | None]:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif fallback_token:
        token = fallback_token
    if not token:
        return None, None
    claims = decode_token_ignore_expiry(token)
    if not claims:
        return None, None
    return claims.get("userId"), claims.get("orgTags", "")


@router.delete("/{file_md5}")
async def delete_file(
    file_md5: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete document. Mirrors DocumentController.deleteDocument."""
    try:
        user_id = user.get("user_id")
        role = user.get("role")

        result = await db.execute(
            select(FileUpload)
            .where(FileUpload.file_md5 == file_md5, FileUpload.user_id == user_id)
            .order_by(FileUpload.created_at.desc()).limit(1)
        )
        f = result.scalar_one_or_none()
        if not f:
            return {"code": 404, "message": "文档不存在"}
        if f.user_id != user_id and role != "ADMIN":
            return {"code": 403, "message": "没有权限删除此文档"}

        await delete_document(db, file_md5, user_id)
        await db.commit()
        return {"code": 200, "message": "文档删除成功"}
    except Exception as e:
        await db.rollback()
        return {"code": 500, "message": f"删除文档失败: {e}"}


@router.post("/{file_md5}/reindex")
async def reindex_document(
    file_md5: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rebuild document index. Simplified - full impl requires Kafka."""
    try:
        user_id = user.get("user_id")
        role = user.get("role")

        result = await db.execute(
            select(FileUpload)
            .where(FileUpload.file_md5 == file_md5)
            .order_by(FileUpload.created_at.desc()).limit(1)
        )
        f = result.scalar_one_or_none()
        if not f:
            return {"code": 404, "message": "文档不存在"}
        if f.user_id != user_id and role != "ADMIN":
            return {"code": 403, "message": "没有权限重建此文档索引"}

        # TODO: Kafka-based reindex (Phase 5)
        return {"code": 200, "message": "文档索引重建已提交", "data": {"fileMd5": file_md5, "fileName": f.file_name}}
    except Exception as e:
        return {"code": 500, "message": f"重建文档索引失败: {e}"}


@router.get("/accessible")
async def accessible_files(
    user: dict = Depends(get_current_user),
    page: int | None = Query(None),
    size: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List accessible files. Mirrors DocumentController.getAccessibleFiles."""
    try:
        user_id = user.get("user_id")
        org_tags = user.get("org_tags", "")

        files = await get_accessible_files(db, user_id, org_tags)
        file_data = []
        for f in files:
            file_data.append({
                "id": f.id, "fileMd5": f.file_md5, "fileName": f.file_name,
                "totalSize": f.total_size, "status": f.status, "userId": f.user_id,
                "orgTag": f.org_tag, "public": f.is_public, "isPublic": f.is_public,
                "createdAt": str(f.created_at) if f.created_at else None,
                "mergedAt": str(f.merged_at) if f.merged_at else None,
                "estimatedEmbeddingTokens": f.estimated_embedding_tokens,
                "estimatedChunkCount": f.estimated_chunk_count,
                "actualEmbeddingTokens": f.actual_embedding_tokens,
                "actualChunkCount": f.actual_chunk_count,
                "vectorizationStatus": f.vectorization_status,
                "vectorizationErrorMessage": f.vectorization_error_message,
                "orgTagName": await get_org_tag_name(db, f.org_tag),
            })

        if page is not None or size is not None:
            p = max(1, page or 1)
            s = max(1, size or 10)
            total = len(file_data)
            start = (p - 1) * s
            content = file_data[start:start + s]
            result_data = {
                "content": content, "data": content, "number": p, "size": s, "totalElements": total,
            }
        else:
            result_data = file_data

        return ResponseWrapper(code=200, message="获取可访问文件列表成功", data=result_data).model_dump()
    except Exception as e:
        return {"code": 500, "message": f"获取可访问文件列表失败: {e}"}


@router.get("/uploads")
async def user_uploads(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's uploaded files. Mirrors DocumentController.getUserUploadedFiles."""
    try:
        user_id = user.get("user_id")
        files = await get_user_uploaded_files(db, user_id)
        file_data = [
            {
                "id": f.id, "fileMd5": f.file_md5, "fileName": f.file_name,
                "totalSize": f.total_size, "status": f.status, "userId": f.user_id,
                "orgTag": f.org_tag, "public": f.is_public, "isPublic": f.is_public,
                "createdAt": str(f.created_at) if f.created_at else None,
                "mergedAt": str(f.merged_at) if f.merged_at else None,
                "vectorizationStatus": f.vectorization_status,
                "orgTagName": await get_org_tag_name(db, f.org_tag),
            }
            for f in files
        ]
        return ResponseWrapper(code=200, message="获取用户上传文件列表成功", data=file_data).model_dump()
    except Exception as e:
        return {"code": 500, "message": f"获取用户上传文件列表失败: {e}"}


@router.get("/download")
async def download_file(
    fileName: str = Query(...),
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Download file by name. Mirrors DocumentController.downloadFileByName."""
    try:
        user_id, org_tags = _resolve_auth(authorization, token)

        if not user_id:
            result = await db.execute(
                select(FileUpload)
                .where(FileUpload.file_name == fileName, FileUpload.is_public == True)
                .order_by(FileUpload.created_at.desc()).limit(1)
            )
        else:
            files = await get_accessible_files(db, user_id, org_tags or "")
            matching = [f for f in files if f.file_name == fileName]
            result = type("R", (), {"scalar_one_or_none": lambda: matching[0] if matching else None})()

        f = result.scalar_one_or_none() if hasattr(result, "scalar_one_or_none") else result
        if not f:
            return {"code": 404, "message": "文件不存在或无权限访问"}

        url = await generate_download_url(f.file_md5)
        return ResponseWrapper(
            code=200, message="文件下载链接生成成功",
            data={"fileName": f.file_name, "downloadUrl": url, "fileSize": f.total_size},
        ).model_dump()
    except Exception as e:
        return {"code": 500, "message": f"文件下载失败: {e}"}


@router.get("/download-by-md5")
async def download_by_md5(
    fileMd5: str = Query(...),
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Download file by MD5. Mirrors DocumentController.downloadFileByMd5."""
    try:
        user_id, org_tags = _resolve_auth(authorization, token)

        if not user_id:
            result = await db.execute(
                select(FileUpload)
                .where(FileUpload.file_md5 == fileMd5, FileUpload.is_public == True)
                .order_by(FileUpload.created_at.desc()).limit(1)
            )
            f = result.scalar_one_or_none()
        else:
            files = await get_accessible_files(db, user_id, org_tags or "")
            f = next((x for x in files if x.file_md5 == fileMd5), None)

        if not f:
            return {"code": 404, "message": "文件不存在或无权限访问"}

        url = await generate_download_url(f.file_md5)
        return ResponseWrapper(
            code=200, message="文件下载链接生成成功",
            data={"fileName": f.file_name, "downloadUrl": url, "fileSize": f.total_size, "fileMd5": f.file_md5},
        ).model_dump()
    except Exception as e:
        return {"code": 500, "message": f"文件下载失败: {e}"}


@router.get("/preview")
async def preview_file(
    fileName: str = Query(...),
    fileMd5: str | None = Query(None),
    pageNumber: int | None = Query(None),
    token: str | None = Query(None),
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Preview file content. Mirrors DocumentController.previewFileByName."""
    try:
        user_id, org_tags = _resolve_auth(authorization, token)

        if not user_id:
            result = await db.execute(
                select(FileUpload)
                .where(FileUpload.file_name == fileName, FileUpload.is_public == True)
                .order_by(FileUpload.created_at.desc()).limit(1)
            )
            f = result.scalar_one_or_none()
        else:
            files = await get_accessible_files(db, user_id, org_tags or "")
            f = next((x for x in files if x.file_md5 == fileMd5 or x.file_name == fileName), None)

        if not f:
            return {"code": 404, "message": "文件不存在或无权限访问"}

        ext = _get_extension(f.file_name)
        preview_type = get_preview_type(ext)

        if preview_type == "text":
            content = await get_file_preview_content(f.file_md5, f.file_name)
            return ResponseWrapper(
                code=200, message="文件预览内容获取成功",
                data={"fileName": f.file_name, "fileMd5": f.file_md5, "fileSize": f.total_size,
                      "previewType": preview_type, "content": content},
            ).model_dump()

        url = await generate_download_url(f.file_md5)
        data = {"fileName": f.file_name, "fileMd5": f.file_md5, "fileSize": f.total_size,
                "previewType": preview_type, "previewUrl": url}

        if preview_type == "pdf" and pageNumber:
            data["previewUrl"] = f"/api/v1/documents/page-preview?fileMd5={quote(f.file_md5)}&pageNumber={pageNumber}"
            data["singlePageMode"] = True
            data["sourcePageNumber"] = pageNumber

        return ResponseWrapper(code=200, message="文件预览内容获取成功", data=data).model_dump()
    except Exception as e:
        return {"code": 500, "message": f"文件预览失败: {e}"}


@router.get("/page-preview")
async def page_preview(
    fileMd5: str = Query(...),
    pageNumber: int = Query(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """PDF single page preview. Mirrors DocumentController.previewPdfPage."""
    try:
        user_id = user.get("user_id")
        org_tags = user.get("org_tags", "")

        files = await get_accessible_files(db, user_id, org_tags)
        f = next((x for x in files if x.file_md5 == fileMd5), None)
        if not f:
            return {"code": 404, "message": "文件不存在或无权限访问"}

        ext = _get_extension(f.file_name)
        if ext != "pdf":
            return {"code": 400, "message": "仅支持 PDF 单页预览"}

        # Check Redis cache first
        r = await get_redis()
        cache_key = f"preview:pdf:single-page:{fileMd5}:{pageNumber}"
        cached = await r.get(cache_key)
        if cached:
            return FastAPIResponse(content=cached, media_type="application/pdf",
                                   headers={"X-Preview-Cache": "HIT", "X-Preview-Page": str(pageNumber)})

        # Fetch from MinIO (full file), extract page would need PyPDF2
        # For now, return the full file URL
        url = generate_presigned_url(file_md5)
        return ResponseWrapper(
            code=200, message="PDF 预览",
            data={"previewUrl": url, "pageNumber": pageNumber, "singlePageMode": True},
        ).model_dump()
    except Exception as e:
        return {"code": 500, "message": f"PDF 单页预览失败: {e}"}
