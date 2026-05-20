"""Document service - file listing, deletion, download, preview."""
import logging

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis import get_redis
from models.document_vector import DocumentVector
from models.file_upload import FileUpload
from models.organization_tag import OrganizationTag
from services.upload import generate_presigned_url, remove_merged_file

logger = logging.getLogger(__name__)

# Supported preview types
TEXT_EXTENSIONS = {"txt", "md", "json", "xml", "csv", "html", "htm", "css", "js",
                   "java", "py", "sql", "yaml", "yml", "c", "cpp", "h", "rs", "go", "ts"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"}
PDF_EXTENSION = "pdf"


def _get_extension(file_name: str | None) -> str:
    if not file_name:
        return ""
    dot = file_name.rfind(".")
    if dot < 0 or dot == len(file_name) - 1:
        return ""
    return file_name[dot + 1:].lower()


def get_preview_type(extension: str) -> str:
    if not extension:
        return "download"
    ext = extension.lower()
    if ext == PDF_EXTENSION:
        return "pdf"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "download"


async def get_accessible_files(db: AsyncSession, user_id: str, org_tags: str) -> list[FileUpload]:
    """Get files accessible by user (own files + public files + same-org files)."""
    tags = [t.strip() for t in org_tags.split(",") if t.strip()] if org_tags else []

    conditions = [FileUpload.user_id == user_id, FileUpload.is_public == True]
    if tags:
        conditions.append(FileUpload.org_tag.in_(tags))

    result = await db.execute(
        select(FileUpload)
        .where(or_(*conditions))
        .order_by(FileUpload.created_at.desc())
    )
    return list(result.scalars().all())


async def get_user_uploaded_files(db: AsyncSession, user_id: str) -> list[FileUpload]:
    result = await db.execute(
        select(FileUpload)
        .where(FileUpload.user_id == user_id)
        .order_by(FileUpload.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_document(db: AsyncSession, file_md5: str, user_id: str) -> None:
    """Delete document: ES index, MySQL vectors + chunks + file_upload, MinIO file."""
    # Delete ES documents (async, best-effort)
    try:
        from clients.elasticsearch_client import get_es
        es = get_es()
        await es.delete_by_query(
            index="knowledge_base",
            body={"query": {"term": {"fileMd5": file_md5}}},
        )
    except Exception as e:
        logger.warning("ES delete failed for %s: %s", file_md5, e)

    # Delete from MySQL
    await db.execute(delete(DocumentVector).where(DocumentVector.file_md5 == file_md5))
    from upload import ChunkInfo
    await db.execute(delete(ChunkInfo).where(ChunkInfo.file_md5 == file_md5))
    await db.execute(delete(FileUpload).where(
        and_(FileUpload.file_md5 == file_md5, FileUpload.user_id == user_id)
    ))

    # Delete from MinIO
    remove_merged_file(file_md5)


async def generate_download_url(file_md5: str) -> str | None:
    try:
        return generate_presigned_url(file_md5)
    except Exception as e:
        logger.error("Failed to generate download URL for %s: %s", file_md5, e)
        return None


async def get_file_preview_content(file_md5: str, file_name: str) -> str | None:
    """Get text preview of a file from MinIO."""
    try:
        from upload import get_merged_file_stream
        stream = get_merged_file_stream(file_md5)
        content = stream.read(1024 * 100)  # Read up to 100KB for preview
        stream.close()
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.error("Preview failed for %s: %s", file_md5, e)
        return None


async def get_org_tag_name(db: AsyncSession, tag_id: str | None) -> str | None:
    if not tag_id:
        return None
    result = await db.execute(select(OrganizationTag).where(OrganizationTag.tag_id == tag_id))
    tag = result.scalar_one_or_none()
    return tag.name if tag else tag_id
