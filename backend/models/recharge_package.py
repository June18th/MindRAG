from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text, text

from core.database import Base


class RechargePackage(Base):
    __tablename__ = "recharge_packages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    package_name = Column("package_name", String(128), nullable=False)
    package_price = Column("package_price", BigInteger, nullable=False)
    package_desc = Column("package_desc", Text)
    package_benefit = Column("package_benefit", Text)
    llm_token = Column("llm_token", Integer, nullable=False)
    embedding_token = Column("embedding_token", Integer, nullable=False)
    sort_order = Column("sort_order", Integer, nullable=False, default=10)
    enabled = Column(Boolean, nullable=False, default=True)
    deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column("updated_at", DateTime, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))
