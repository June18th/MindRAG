from sqlalchemy import BigInteger, Column, DateTime, Enum, Integer, String, text

from core.database import Base


class RechargeOrder(Base):
    __tablename__ = "recharge_orders"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_no = Column("trade_no", String(128), nullable=False, unique=True)
    user_id = Column("user_id", String(64), nullable=False)
    package_id = Column("package_id", Integer, nullable=False)
    amount = Column(BigInteger, nullable=False)
    llm_token = Column("llm_token", Integer, nullable=False)
    embedding_token = Column("embedding_token", Integer, nullable=False)
    wx_transaction_id = Column("wx_transaction_id", String(64))
    status = Column(String(16), nullable=False, default="NOT_PAY")
    description = Column(String(255))
    pay_time = Column("pay_time", DateTime)
    created_at = Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column("updated_at", DateTime, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))
