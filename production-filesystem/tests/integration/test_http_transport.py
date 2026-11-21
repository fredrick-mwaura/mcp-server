"""Integration tests for fail-closed streamable HTTP authentication."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mcp_fs.config import Settings
from mcp_fs.server import build_server


def test_http_transport_settings_from_env(monkeypatch) -> None:
    """Settings should correctly read host, port, and transport from env."""
    monkeypatch.setenv("MCP_FS_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_FS_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_FS_PORT", "9090")
    monkeypatch.setenv("MCP_FS_AUTH_TOKEN", "test-token-at-least-16-bytes")

    settings = Settings.from_env()
    assert settings.transport == "streamable-http"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9090
    assert settings.effective_auth_mode == "bearer"


def test_remote_http_without_token_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("MCP_FS_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_FS_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="token"):
        Settings.from_env()


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


def test_bearer_auth_rejects_missing_and_accepts_correct_token(tmp_path: Path) -> None:
    token = "correct-token-at-least-16-bytes"
    settings = Settings(
        roots=[str(tmp_path)], transport="streamable-http", host="127.0.0.1",
        auth_mode="bearer", auth_token=token,
    )
    app = build_server(settings).streamable_http_app(host=settings.host)
    request = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/mcp", json=request).status_code == 401
        assert client.post("/mcp", json=request, headers={"Authorization": "Bearer wrong-token-0000"}).status_code == 401
        response = client.post("/mcp", json=request, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code not in {401, 403}
