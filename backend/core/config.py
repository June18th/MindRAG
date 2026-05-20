from pathlib import Path

from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    # Server
    server_port: int = 8081
    app_timezone: str = "Asia/Shanghai"

    # MySQL
    spring_datasource_url: str = ""
    spring_datasource_username: str = "root"
    spring_datasource_password: str = ""

    # Redis
    spring_data_redis_host: str = "localhost"
    spring_data_redis_port: int = 6379
    spring_data_redis_password: str = ""

    # Kafka
    spring_kafka_bootstrap_servers: str = "127.0.0.1:9092"
    spring_kafka_topic_partitions: int = 1
    spring_kafka_topic_replication_factor: int = 1

    # MinIO
    minio_endpoint: str = "http://localhost:9000"
    minio_public_url: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket_name: str = "uploads"

    # Elasticsearch
    elasticsearch_host: str = "localhost"
    elasticsearch_port: int = 9200
    elasticsearch_scheme: str = "http"
    elasticsearch_username: str = "elastic"
    elasticsearch_password: str = ""
    elasticsearch_insecure_trust_all_certificates: bool = True
    elasticsearch_init_enable: bool = True

    # JWT (must be Base64-encoded)
    jwt_secret_key: str = ""

    # Admin bootstrap
    admin_bootstrap_enabled: bool = False
    admin_bootstrap_username: str = "admin"
    admin_bootstrap_password: str = ""
    admin_bootstrap_primary_org: str = "default"
    admin_bootstrap_org_tags: str = "default,admin"

    # Registration
    app_auth_registration_mode: str = "INVITE_ONLY"
    app_auth_invite_required: bool = True

    # Rate limiting
    rate_limit_register_max: int = 20
    rate_limit_register_window_seconds: int = 600
    rate_limit_login_max: int = 30
    rate_limit_login_window_seconds: int = 60
    rate_limit_chat_message_max: int = 30
    rate_limit_chat_message_window_seconds: int = 60

    # LLM
    deepseek_api_url: str = "https://api.deepseek.com/v1"
    deepseek_api_model: str = "deepseek-chat"
    deepseek_api_key: str = ""

    # Embedding
    embedding_api_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_model: str = "text-embedding-v4"
    embedding_api_key: str = ""
    embedding_batch_size: int = 10
    embedding_dimension: int = 2048

    # CORS
    security_allowed_origins: str = "http://localhost:*,http://127.0.0.1:*"

    # Log
    log_level_app: str = "INFO"

    # WeChat Pay
    wx_pay_enable: bool = False
    wx_pay_app_id: str = ""
    wx_pay_merchant_id: str = ""
    wx_pay_private_key: str = ""
    wx_pay_merchant_serial_number: str = ""
    wx_pay_api_v3_key: str = ""
    wx_pay_notify_url: str = ""
    wx_pay_refund_notify_url: str = ""

    # File parsing
    file_parsing_pdf_engine: str = "liteparse"
    file_parsing_liteparse_command: str = "lit"
    file_parsing_liteparse_ocr_enabled: bool = False
    file_parsing_liteparse_ocr_language: str = "chi_sim"
    file_parsing_liteparse_dpi: int = 300
    file_parsing_liteparse_max_pages: int = 1000
    file_parsing_liteparse_timeout_seconds: int = 300
    file_parsing_liteparse_tessdata_path: str = ""

    # Aliyun OCR
    aliyun_ocr_enabled: bool = False
    aliyun_ocr_endpoint: str = "ocr-api.cn-hangzhou.aliyuncs.com"
    aliyun_ocr_access_key_id: str = ""
    aliyun_ocr_access_key_secret: str = ""
    aliyun_ocr_type: str = "Advanced"
    aliyun_ocr_callback_token: str = ""

    @property
    def mysql_dsn(self) -> str | None:
        """Extract clean MySQL DSN from Spring datasource URL."""
        url = self.spring_datasource_url
        if not url:
            return None
        # jdbc:mysql://host:port/db?... -> mysql+asyncmy://host:port/db
        url = url.replace("jdbc:mysql://", "")
        params_start = url.find("?")
        host_db = url[:params_start] if params_start != -1 else url
        return f"mysql+asyncmy://{self.spring_datasource_username}:{self.spring_datasource_password}@{host_db}?charset=utf8mb4"

    @property
    def es_hosts(self) -> str:
        return f"{self.elasticsearch_scheme}://{self.elasticsearch_host}:{self.elasticsearch_port}"

    model_config = {"env_file": str(_PROJECT_ROOT / ".env"), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
