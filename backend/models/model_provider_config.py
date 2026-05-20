from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String, text

from core.database import Base


class ModelProviderConfig(Base):
    __tablename__ = "model_provider_configs"
    __table_args__ = (
        Index("idx_model_provider_scope", "config_scope"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    config_scope = Column("config_scope", String(32), nullable=False)
    provider_code = Column("provider_code", String(64), nullable=False)
    display_name = Column("display_name", String(128), nullable=False)
    api_style = Column("api_style", String(64), nullable=False)
    api_base_url = Column("api_base_url", String(512), nullable=False)
    model_name = Column("model_name", String(255), nullable=False)
    api_key_ciphertext = Column("api_key_ciphertext", String(2048))
    embedding_dimension = Column("embedding_dimension", Integer)
    enabled = Column(Boolean, nullable=False, default=True)
    active = Column(Boolean, nullable=False, default=False)
    updated_by = Column("updated_by", String(255), nullable=False)
    created_at = Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column("updated_at", DateTime, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))
