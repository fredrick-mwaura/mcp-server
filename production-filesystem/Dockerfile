# ==============================================================================
# Multi-Stage Dockerfile for mcp-filesystem-server (SWE-Bench Evaluation Ready)
# ==============================================================================

# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Final Runtime
FROM python:3.12-slim AS runner

# Install git and ripgrep for fast code search and SWE-bench git operations
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ripgrep \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root user and workspace directory
RUN useradd -u 1000 -m -s /bin/bash mcpuser && \
    mkdir -p /workspace && \
    chown -R mcpuser:mcpuser /workspace

USER mcpuser
WORKDIR /workspace

# Configure default environment for headless container execution
ENV PYTHONUNBUFFERED=1 \
    MCP_FS_ROOTS=/workspace \
    MCP_FS_MODE=read-write \
    MCP_FS_TRANSPORT=streamable-http \
    MCP_FS_HOST=0.0.0.0 \
    MCP_FS_PORT=8000

EXPOSE 8000

ENTRYPOINT ["mcp-fs"]
