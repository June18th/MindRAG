from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, UniqueConstraint, text

from core.database import Base


class FileUpload(Base):
    __tablename__ = "file_upload"
    __table_args__ = (
        UniqueConstraint("file_md5", "user_id", name="uk_file_upload_md5_user"),
    )

    STATUS_UPLOADING = 0
    STATUS_COMPLETED = 1
    STATUS_MERGING = 2

    VECTORIZATION_PENDING = "PENDING"
    VECTORIZATION_PROCESSING = "PROCESSING"
    VECTORIZATION_COMPLETED = "COMPLETED"
    VECTORIZATION_FAILED = "FAILED"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    file_md5 = Column("file_md5", String(32), nullable=False)
    file_name = Column("file_name", String(255), nullable=False)
    total_size = Column("total_size", BigInteger, nullable=False)
    status = Column(Integer, nullable=False, default=0)
    user_id = Column("user_id", String(64), nullable=False)
    org_tag = Column("org_tag", String(50))
    is_public = Column("is_public", Boolean, nullable=False, default=False)
    estimated_embedding_tokens = Column("estimated_embedding_tokens", BigInteger)
    estimated_chunk_count = Column("estimated_chunk_count", Integer)
    actual_embedding_tokens = Column("actual_embedding_tokens", BigInteger)
    actual_chunk_count = Column("actual_chunk_count", Integer)
    vectorization_status = Column("vectorization_status", String(32))
    vectorization_error_message = Column("vectorization_error_message", String(1000))
    created_at = Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    merged_at = Column("merged_at", DateTime, onupdate=text("CURRENT_TIMESTAMP"))
