from sqlalchemy import BigInteger, Column, Integer, String, UniqueConstraint

from core.database import Base


class ChunkInfo(Base):
    __tablename__ = "chunk_info"
    __table_args__ = (
        UniqueConstraint("file_md5", "chunk_index", name="uk_file_md5_chunk_index"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    file_md5 = Column("file_md5", String(32), nullable=False)
    chunk_index = Column("chunk_index", Integer, nullable=False)
    chunk_md5 = Column("chunk_md5", String(32), nullable=False)
    storage_path = Column("storage_path", String(255), nullable=False)
