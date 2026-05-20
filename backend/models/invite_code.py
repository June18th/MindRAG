from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import relationship

from core.database import Base


class InviteCode(Base):
    __tablename__ = "invite_codes"
    __table_args__ = (
        Index("idx_invite_code_code", "code", unique=True),
        Index("idx_invite_code_enabled", "enabled"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    code = Column(String(64), nullable=False, unique=True)
    max_uses = Column(Integer, name="max_uses", nullable=False)
    used_count = Column(Integer, name="used_count", nullable=False, default=0)
    expires_at = Column(DateTime, name="expires_at")
    enabled = Column(Boolean, nullable=False, default=True)
    created_by = Column(BigInteger, ForeignKey("users.id"), nullable=False, name="created_by")
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )

    created_by_user = relationship("User", back_populates="invite_codes")
