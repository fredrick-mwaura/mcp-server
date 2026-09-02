"""Integration tests for streamable-http transport without authentication.

Validates that:
  - Settings properly parse MCP_FS_HOST, MCP_FS_PORT, and MCP_FS_TRANSPORT.
  - The streamable_http_app operates without requiring authentication (zero-auth).
  - TestClient can connect to the /mcp endpoint with standard headers.
"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from mcp_fs.config import Settings
from mcp_fs.server import build_server


def test_http_transport_settings_from_env(monkeypatch) -> None:
    """Settings should correctly read host, port, and transport from env."""
    monkeypatch.setenv("MCP_FS_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_FS_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_FS_PORT", "9090")

    settings = Settings.from_env()
    assert settings.transport == "streamable-http"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9090


def test_http_transport_zero_auth_accessible(tmp_path: Path) -> None:
    """The /mcp endpoint must be accessible without any credentials."""
    settings = Settings(
        roots=[str(tmp_path)],
        mode="read-write",
        transport="streamable-http",
        host="127.0.0.1",
        port=8000,
    )
    server = build_server(settings)
    app = server.streamable_http_app(host=settings.host)

    with TestClient(app, base_url=f"http://{settings.host}:{settings.port}") as client:
        # Send a ping/probe to the MCP endpoint
        response = client.post(
            "/mcp",
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        # It should not return 401 or 403 (no auth barrier)
        assert response.status_code != 401
        assert response.status_code != 403
