FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        wireguard-tools \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
# Install deps only — the source is copied next and made importable via
# PYTHONPATH. This keeps the deps in their own layer for caching.
RUN pip install --upgrade pip \
 && pip install \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "jinja2>=3.1" \
      "python-multipart>=0.0.12" "pydantic>=2.9" "ruamel.yaml>=0.18" \
      "sqlmodel>=0.0.22" "apscheduler>=3.10" "httpx>=0.28" \
      "filelock>=3.16" "watchfiles>=0.24" "qrcode[pil]>=8.0" "cryptography>=43.0"

COPY src ./src
COPY templates ./templates
COPY static ./static

ENV PYTHONPATH=/app/src

EXPOSE 8080
CMD ["uvicorn", "gdlf.main:app", "--host", "0.0.0.0", "--port", "8080"]
