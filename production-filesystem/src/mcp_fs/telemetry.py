"""Logging & telemetry setup.

LESSON (carried over from Lesson 1): the MCP stdio transport uses STDOUT for
protocol messages, so ALL human/machine logs go to STDERR.

The Phase 1 upgrade: logs are emitted as single-line JSON by default. Why?
  - Each log line is a queryable event for a log aggregator (Splunk, Loki, ...).
  - Extra fields (tool name, requested path, duration) arrive as structured
    key/value pairs instead of text you have to regex.
  - It is exactly the same stream an operator reads in production.

`configure_logging()` is idempotent and safe to call at startup. The "tool
call" event we log lives in server.py (it has access to the timing), and
payloads are attached with logging's `extra={...}` mechanism.
"""

from __future__ import annotations

import logging
import sys

# Logging-record attributes that belong to the logging machinery itself. When
# we format an event's `extra={...}` payload we must skip these, otherwise we
# would emit bookkeeping noise instead of the fields we actually added.
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName", "processName",
    "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    """Emit each log record as one line of JSON on stderr."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload: dict[str, object] = {
            "ts": _iso_time(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Fold in any structured fields passed via logger.info(..., extra={...}).
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = _jsonable(value)
        # Attach exception tracebacks the way operators expect to see them.
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _iso_time(epoch: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()


def _jsonable(value: object) -> object:
    """Small guard so exotic extra values never crash the formatter."""
    return str(value) if isinstance(value, (bytes, bytearray)) else value


def configure_logging(level: str = "INFO", log_format: str = "json") -> logging.Logger:
    """Install a single handler on the root logger (writes to STDERR).

    Returns the package logger, ready for `logger.info("event", extra={...})`.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    # Clear existing handlers so tests / repeated startup never stack handlers.
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)  # <-- NEVER stdout
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
        )
    root.addHandler(handler)

    return logging.getLogger("mcp_fs")
