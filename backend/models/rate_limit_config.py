from sqlalchemy import BigInteger, Column, DateTime, Integer, String, text

from core.database import Base


class RateLimitConfig(Base):
    __tablename__ = "rate_limit_configs"

    config_key = Column("config_key", String(64), primary_key=True)
    single_max = Column("single_max", Integer)
    single_window_seconds = Column("single_window_seconds", BigInteger)
    minute_max = Column("minute_max", BigInteger)
    minute_window_seconds = Column("minute_window_seconds", BigInteger)
    day_max = Column("day_max", BigInteger)
    day_window_seconds = Column("day_window_seconds", BigInteger)
    updated_by = Column("updated_by", String(255), nullable=False)
    created_at = Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column("updated_at", DateTime, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))
