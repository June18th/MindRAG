# Python backend Dockerfile
FROM python:3.11-slim

WORKDIR /knowhub/backend

# System dependencies (use Aliyun mirror for China)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (use Aliyun PyPI mirror)
COPY pyproject.toml .
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir -e ".[dev]" 2>/dev/null || \
    pip install --no-cache-dir \
    fastapi>=0.115.0 \
    uvicorn[standard]>=0.30.0 \
    sqlalchemy[asyncio]>=2.0.30 \
    asyncmy>=0.2.9 \
    redis[hiredis]>=5.0.0 \
    elasticsearch[async]>=8.14.0 \
    aiokafka>=0.11.0 \
    minio>=7.2.0 \
    PyJWT>=2.8.0 \
    httpx>=0.27.0 \
    bcrypt>=4.0.0 \
    pydantic>=2.0.0 \
    pydantic-settings>=2.0.0 \
    python-multipart>=0.0.9 \
    cryptography>=42.0.0 \
    orjson>=3.10.0 \
    langchain>=0.3.0 \
    langchain-core>=0.3.0 \
    langchain-openai>=0.2.0 \
    langchain-elasticsearch>=0.3.0 \
    langgraph>=0.2.0

# Copy prompts (editable without rebuild)
COPY backend/prompts/ ./prompts/

# Copy application code
COPY backend/ ./

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
