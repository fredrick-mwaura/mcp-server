FROM rust:1.100.0-bookworm AS builder

WORKDIR /build
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY src ./src
RUN cargo build --locked --release

FROM debian:bookworm-slim AS runner

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin mcpuser \
    && mkdir -p /workspace \
    && chown mcpuser:mcpuser /workspace

COPY --from=builder /build/target/release/mcp-filesystem-server /usr/local/bin/mcp-filesystem-server

USER 1000:1000
WORKDIR /workspace

ENV MCP_FS_ROOTS=/workspace \
    MCP_FS_MODE=read-only \
    MCP_FS_ALLOW_EXEC=false \
    MCP_FS_TRANSPORT=streamable-http \
    MCP_FS_HOST=0.0.0.0 \
    MCP_FS_PORT=8000 \
    MCP_FS_AUTH_MODE=auto

EXPOSE 8000

ENTRYPOINT ["mcp-filesystem-server"]
