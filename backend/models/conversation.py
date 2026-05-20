from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship

from core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_user_id", "user_id"),
        Index("idx_conversation_id", "conversation_id"),
        Index("idx_timestamp", "timestamp"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    conversation_id = Column("conversation_id", String(64))
    reference_mappings_json = Column("reference_mappings_json", Text)
    timestamp = Column(DateTime, default=datetime.now)

    user = relationship("User", back_populates="conversations")
