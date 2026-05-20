from datetime import date, datetime

from sqlalchemy import BigInteger, Column, Date, DateTime, Index, Integer, String, text

from core.database import Base


class UserTokenRecord(Base):
    __tablename__ = "user_token_record"
    __table_args__ = (
        Index("idx_user_date", "userId", "recordDate"),
        Index("idx_record_date", "recordDate"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, name="userId")
    record_date = Column(Date, nullable=False, name="recordDate")
    token_type = Column(String(20), nullable=False, name="tokenType")
    change_type = Column(String(20), nullable=False, name="changeType")
    amount = Column(BigInteger, nullable=False, default=0)
    balance_before = Column(BigInteger, name="balanceBefore")
    balance_after = Column(BigInteger, name="balanceAfter")
    reason = Column(String(500))
    remark = Column(String(500))
    request_count = Column(BigInteger, nullable=False, default=0, name="requestCount")
    created_at = Column(DateTime, name="createdAt", server_default=text("CURRENT_TIMESTAMP"))
