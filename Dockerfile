# ── Stage 1: Build Next.js UI (static export) ────────────────────────────────
FROM node:20-alpine AS ui-builder

WORKDIR /app/ui

COPY ui/package*.json ./
RUN npm ci --prefer-offline

COPY ui/ ./
RUN npm run build


# ── Stage 2: Python backend ───────────────────────────────────────────────────
FROM python:3.12-slim

# System deps needed by some packages (git for PR tooling, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache friendly)
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# Copy source code and assets
COPY src/     ./src/
COPY prompt/  ./prompt/
COPY config/  ./config/

# Copy the built static UI from stage 1
COPY --from=ui-builder /app/ui/out ./ui/out/

# Persistent data volume mount point
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "-m", "src.main", "serve"]
