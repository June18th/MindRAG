from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import relationship

from core.database import Base


class OrganizationTag(Base):
    __tablename__ = "organization_tags"

    tag_id = Column(String(255), primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    parent_tag = Column(String(255), ForeignKey("organization_tags.tag_id", ondelete="SET NULL"))
    upload_max_size_bytes = Column(BigInteger)
    created_by = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )

    created_by_user = relationship("User", back_populates="organization_tags")
