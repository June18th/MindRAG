from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum, Index, Integer, String, text
from sqlalchemy.orm import relationship

from core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(255), nullable=False, unique=True, index=True)
    password = Column(String(255), nullable=False)
    role = Column(Enum("USER", "ADMIN", "TEST", name="user_role"), nullable=False, default="USER")
    org_tags = Column(String(255), name="org_tags")
    primary_org = Column(String(50), name="primary_org")
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )

    invite_codes = relationship("InviteCode", back_populates="created_by_user")
    organization_tags = relationship("OrganizationTag", back_populates="created_by_user")
    conversations = relationship("Conversation", back_populates="user")
    conversation_sessions = relationship("ConversationSession", back_populates="user")
