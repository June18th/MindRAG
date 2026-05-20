from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text

from core.database import Base


class DocumentVector(Base):
    __tablename__ = "document_vectors"

    vector_id = Column(BigInteger, primary_key=True, autoincrement=True)
    file_md5 = Column("file_md5", String(32), nullable=False)
    chunk_id = Column("chunk_id", Integer, nullable=False)
    text_content = Column("text_content", Text)
    page_number = Column("page_number", Integer)
    anchor_text = Column("anchor_text", String(512))
    model_version = Column("model_version", String(32))
    user_id = Column("user_id", String(64), nullable=False)
    org_tag = Column("org_tag", String(50))
    is_public = Column("is_public", Boolean, nullable=False, default=False)
