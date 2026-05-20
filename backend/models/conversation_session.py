from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import relationship

from core.database import Base


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"
    __table_args__ = (
        Index("idx_cs_user_id", "user_id"),
        Index("idx_cs_conversation_id", "conversation_id", unique=True),
        Index("idx_cs_status", "status"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    conversation_id = Column("conversation_id", String(64), nullable=False, unique=True)
    title = Column(String(255), default="新对话")
    status = Column(String(16), nullable=False, default="ACTIVE")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("User", back_populates="conversation_sessions")
