# =============================================================
# DeepCode - Docker Build
# Multi-stage: Frontend build → Final image with Python + Node
# =============================================================

# ------ Stage 1: Build frontend static assets ------
FROM node:18-alpine AS frontend-builder

WORKDIR /build
COPY new_ui/frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY new_ui/frontend/ ./
RUN npm run build


# ------ Stage 2: Final image ------
FROM python:3.10-slim

# Metadata
LABEL maintainer="DeepCode Team"
LABEL description="DeepCode - AI Research Engine"
LABEL version="1.0"

# Environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEEPCODE_ENV=docker \
    DEEPCODE_HOST=0.0.0.0 \
    DEEPCODE_PORT=8000

# Install system dependencies:
#   - git: for git clone operations in workflows
#   - nodejs/npm/npx: for MCP servers (brave-search, filesystem, fetch)
#   - curl: for health checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates && \
    # Install Node.js 18 via official binary (includes npm + npx)
    ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then NODE_ARCH="arm64"; else NODE_ARCH="x64"; fi && \
    curl -fsSL https://nodejs.org/dist/v18.20.8/node-v18.20.8-linux-${NODE_ARCH}.tar.gz \
        | tar -xz -C /usr/local --strip-components=1 && \
    # Install uv (Python package installer, used by mcp-server-fetch)
    pip install --no-cache-dir uv && \
    # Cleanup
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # Verify
    node --version && npm --version && npx --version

WORKDIR /app

# Install Python dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-install npx MCP server packages (avoid download at runtime)
RUN npx -y @modelcontextprotocol/server-brave-search --help 2>/dev/null || true && \
    npx -y @modelcontextprotocol/server-filesystem --help 2>/dev/null || true

# Copy project source code
COPY __init__.py setup.py deepcode.py ./
COPY config/ ./config/
COPY prompts/ ./prompts/
COPY schema/ ./schema/
COPY tools/ ./tools/
COPY utils/ ./utils/
COPY workflows/ ./workflows/
COPY cli/ ./cli/
COPY ui/ ./ui/
COPY new_ui/backend/ ./new_ui/backend/

# Copy frontend build output from Stage 1
COPY --from=frontend-builder /build/dist ./new_ui/frontend/dist

# Create runtime directories
RUN mkdir -p deepcode_lab uploads logs

# Copy entrypoint script
COPY deepcode_docker/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
