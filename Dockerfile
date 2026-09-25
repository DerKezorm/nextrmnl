# nextrmnl as a single container.
#
# Two stages: the interface is built first, then only the finished files go into the image. Node and
# node_modules stay out; at runtime FastAPI serves the built files itself.

# --- Stage 1: build the interface ------------------------------------------------------------------
#
# --platform=$BUILDPLATFORM: the image is built for amd64 and arm64. Without it this stage would run under
# emulation too, and "npm ci" under emulated ARM is very slow. Only /build/dist moves on from here.
FROM --platform=$BUILDPLATFORM node:22-alpine AS interface

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# --- Stage 2: runtime ------------------------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXTRMNL_DATA_DIR=/data \
    NEXTRMNL_FRONTEND_DIST=/app/static

WORKDIR /app

# curl for the healthcheck, gosu to drop root at start.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gosu \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=interface /build/dist ./static

# The user nextrmnl runs as. The container starts as root and drops the rights in the entrypoint.
RUN useradd --system --create-home --uid 1000 nextrmnl \
    && mkdir -p /data \
    && chown -R nextrmnl:nextrmnl /data /app

COPY docker/entrypoint.sh /entrypoint.sh
# Strips Windows line endings: checked out on Windows the script would not start otherwise.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${NEXTRMNL_PORT:-8000}/api/health" || exit 1

# One worker: SQLite, the open vaults live in this process, and a WebSocket session belongs to one process.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
