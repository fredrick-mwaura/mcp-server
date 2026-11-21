import pytest
from pydantic import ValidationError

from mcp_fs.config import Settings


def test_bearer_secret_is_redacted_from_settings_repr(tmp_path) -> None:
    token = "secret-token-at-least-16-bytes"
    settings = Settings(
        roots=[str(tmp_path)], transport="streamable-http", auth_mode="bearer",
        auth_token=token,
    )
    assert token not in repr(settings)
    assert token not in str(settings)


def test_remote_unsafe_mode_requires_explicit_override(tmp_path) -> None:
    with pytest.raises(ValidationError, match="ALLOW_UNSAFE_REMOTE"):
        Settings(
            roots=[str(tmp_path)], transport="streamable-http", host="0.0.0.0",
            auth_mode="none",
        )
    settings = Settings(
        roots=[str(tmp_path)], transport="streamable-http", host="0.0.0.0",
        auth_mode="none", allow_unsafe_remote=True,
    )
    assert settings.effective_auth_mode == "none"


def test_numeric_limits_are_positive(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Settings(roots=[str(tmp_path)], max_output_bytes=0)
