# ---- SPA build ----
FROM node:20-slim AS web
WORKDIR /web

COPY web/package.json web/package-lock.json* ./
# --legacy-peer-deps: @heroui/theme's >=4.0.0 tailwindcss peer constraint is
# overly strict — the plugin works fine against Tailwind 3.4.
RUN npm install --no-audit --no-fund --legacy-peer-deps

COPY web/ ./
RUN npm run build

# ---- Python runtime ----
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
# Install deps only — source is copied next and made importable via
# PYTHONPATH. This keeps deps in their own cacheable layer.
RUN pip install --upgrade pip \
 && pip install \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" \
      "python-multipart>=0.0.12" "pydantic>=2.9" "ruamel.yaml>=0.18" \
      "sqlmodel>=0.0.22" "apscheduler>=3.10" "httpx[http2]>=0.28" \
      "filelock>=3.16" "watchfiles>=0.24" "qrcode[pil]>=8.0" "cryptography>=43.0" \
      "sse-starlette>=2.1" "tldextract>=5.1"

COPY src ./src
COPY --from=web /web/dist ./web

ENV PYTHONPATH=/app/src

EXPOSE 8080
CMD ["uvicorn", "gdlf.main:app", "--host", "0.0.0.0", "--port", "8080"]
