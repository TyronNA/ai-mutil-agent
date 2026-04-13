# ── Stage 1: Build Next.js UI (static export) ────────────────────────────────
FROM node:20-alpine AS ui-builder

WORKDIR /app/ui

COPY ui/package*.json ./
RUN npm ci --prefer-offline

COPY ui/ ./
RUN npm run build


# ── Stage 2: UI static server (mirrors Next.js dev at :3001) ─────────────────
# Dùng `serve` để host static export — giống `npm run dev` nhưng production-safe
FROM node:20-alpine AS ui

RUN npm install -g serve

WORKDIR /app
COPY --from=ui-builder /app/ui/out ./out

EXPOSE 3001
CMD ["serve", "-s", "out", "-l", "3001"]


# ── Stage 3: Python backend (FastAPI tại :8000) ───────────────────────────────
FROM python:3.12-slim AS backend

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

COPY src/     ./src/
COPY prompt/  ./prompt/
COPY config/  ./config/

RUN mkdir -p /app/data

EXPOSE 8000
CMD ["python", "-m", "src.main", "serve"]
