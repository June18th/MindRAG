<div align="center">

# KnowHub（智枢）

一个企业级的 AI 知识库管理系统，采用检索增强生成（RAG）技术，提供智能文档处理和检索能力。

</div>

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| AI Agent | LangChain |
| LLM | DeepSeek（OpenAI 兼容） |
| Embedding | DashScope text-embedding-v4 |
| ORM | SQLAlchemy |
| 数据库 | MySQL |
| 缓存 | Redis |
| 搜索引擎 | Elasticsearch + IK 分词 |
| 消息队列 | Kafka |
| 文件存储 | MinIO |
| 认证 | JWT |
| 实时通信 | WebSocket |


## 项目结构

```
backend/
├── main.py                # FastAPI 入口 + 管理员启动引导
├── core/                  # 基础设施
│   ├── config.py          # pydantic-settings（读取 .env）
│   ├── security.py        # JWT 编解码（HS256）
│   ├── database.py        # SQLAlchemy 异步引擎
│   ├── redis.py           # Redis 连接池
│   ├── deps.py            # FastAPI 依赖注入
│   └── exceptions.py      # 自定义异常
├── models/                # SQLAlchemy 模型（14 张表）
├── schemas/               # Pydantic 请求/响应模型
├── api/v1/                # REST API 路由（61 个端点）
│   ├── auth.py            # 认证（登录/刷新 Token）
│   ├── users.py           # 用户管理（注册/个人信息/组织标签）
│   ├── upload.py          # 文件分片上传
│   ├── documents.py       # 文档 CRUD/下载/预览
│   ├── search.py          # Elasticsearch 混合检索（KNN + BM25）
│   ├── chat.py            # WebSocket Token/反馈
│   ├── conversation.py    # 会话 CRUD/历史
│   ├── admin.py           # 管理后台（用户/限流/模型配置）
│   └── recharge.py        # 充值套餐/订单
├── services/              # 业务逻辑层
├── clients/               # 外部客户端（MinIO, Elasticsearch, Embedding）
├── websocket/             # WebSocket /chat/{token}
├── prompts/               # System Prompt（.md 可编辑）
└── tests/                 # 测试
```

## 快速开始

### 前置环境

- Python 3.11+
- Docker（运行基础服务）
- pnpm（前端）

### 1. 启动基础服务

```bash
docker-compose up -d mysql redis kafka minio es
```

### 2. 初始化数据库

```bash
# 创建数据库
docker exec -it knowhub_mysql mysql -uroot -pKnowHub2025 \
  -e "CREATE DATABASE IF NOT EXISTS KnowHub DEFAULT CHARACTER SET utf8mb4;"

# 导入表结构
cat docs/databases/ddl.sql | docker exec -i knowhub_mysql mysql -uroot -pKnowHub2025 KnowHub

# 补充 JPA 自动生成的表和列
docker exec -i knowhub_mysql mysql -uroot -pKnowHub2025 KnowHub <<'SQL'
CREATE TABLE IF NOT EXISTS conversations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
    conversation_id VARCHAR(64), reference_mappings_json LONGTEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL, conversation_id VARCHAR(64) NOT NULL UNIQUE,
    title VARCHAR(255) DEFAULT '新对话', status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS invite_codes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(64) NOT NULL UNIQUE, max_uses INT NOT NULL,
    used_count INT NOT NULL DEFAULT 0, expires_at TIMESTAMP NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id)
);
ALTER TABLE file_upload ADD COLUMN IF NOT EXISTS vectorization_status VARCHAR(32);
ALTER TABLE file_upload ADD COLUMN IF NOT EXISTS vectorization_error_message VARCHAR(1000);
SQL

# 写入种子数据（模型配置）
docker exec -i knowhub_mysql mysql -uroot -pKnowHub2025 KnowHub <<'SQL'
INSERT IGNORE INTO model_provider_configs
  (config_scope, provider_code, display_name, api_style, api_base_url, model_name, enabled, active, updated_by)
VALUES
  ('llm', 'deepseek', 'DeepSeek', 'openai', 'https://api.deepseek.com/v1', 'deepseek-chat', 1, 1, 'admin'),
  ('embedding', 'dashscope', 'DashScope Embedding', 'openai', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'text-embedding-v4', 1, 1, 'admin');
SQL
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

关键配置项：
- `DEEPSEEK_API_KEY` — LLM API 密钥
- `EMBEDDING_API_KEY` — Embedding API 密钥
- `JWT_SECRET_KEY` — Base64 编码的 JWT 签名密钥

### 4. 安装依赖

```bash
pip install fastapi uvicorn sqlalchemy asyncmy redis elasticsearch aiokafka minio PyJWT httpx bcrypt pydantic pydantic-settings python-multipart orjson langchain langchain-core langchain-openai langchain-elasticsearch langgraph cryptography
```

### 5. 启动后端

```bash
python backend/main.py
```

后端：`http://localhost:8000` | API 文档：`http://localhost:8000/docs`

### 6. 启动前端

```bash
cd frontend && pnpm install && pnpm dev
```

前端：`http://localhost:9527`

## Docker 部署

```bash
docker-compose up -d --build
```

| 服务 | 端口 | 容器名 |
|------|------|--------|
| 前端 | 80 | knowhub_frontend |
| 后端 | 8000 | knowhub_backend |
| MySQL | 3307 | knowhub_mysql |
| Redis | 6379 | knowhub_redis |
| Kafka | 9092 | knowhub_kafka |
| MinIO | 9000 | knowhub_minio |
| Elasticsearch | 9201 | knowhub_es |

## 登录账号

- 管理员：`admin` / `KnowHub@2025`
- 注册模式默认 OPEN，可直接注册

## API 概览（61 端点）

| 模块 | 端点数 | 示例 |
|------|--------|------|
| Auth | 2 | 登录、刷新 Token |
| Users | 10 | 注册、个人信息、组织标签、用量 |
| Upload | 4 | 分片上传、合并 |
| Documents | 8 | 文档 CRUD、下载、预览 |
| Search | 1 | Elasticsearch 混合检索 |
| Chat HTTP | 4 | WebSocket Token、反馈 |
| Conversation | 7 | 会话创建/归档、历史查询 |
| Admin | 21 | 用户管理、限流、模型配置 |
| Recharge | 2 | 套餐列表、订单 |
| WebSocket | 1 | `/chat/{token}` |
