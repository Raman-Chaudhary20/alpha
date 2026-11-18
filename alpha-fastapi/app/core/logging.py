"""Structured logging configuration for Alpha.

Call ``setup_logging()`` once at application startup.  Subsequent callers
obtain the same logger via ``get_logger(name)``.

JSON output is activated when the ``LOG_FORMAT`` environment variable is set
to ``"json"`` (or when ``json_logs=True`` is passed to ``setup_logging``).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = traceback.format_exception(*record.exc_info)
        # forward any extra keys attached to the record
        for key, value in record.__dict__.items():
            if key not in {
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "message", "module", "msecs", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "taskName", "thread", "threadName",
            }:
                payload[key] = value
        return json.dumps(payload, default=str)


class _TextFormatter(logging.Formatter):
    """Human-readable formatter with optional colour."""

    _COLOURS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[1;31m",
    }
    _RESET = "\033[0m"

    def __init__(self, use_colour: bool = True) -> None:
        super().__init__()
        self._use_colour = use_colour and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        level = record.levelname
        if self._use_colour:
            colour = self._COLOURS.get(level, "")
            level = f"{colour}{level:<8}{self._RESET}"
        else:
            level = f"{level:<8}"
        msg = record.getMessage()
        base = f"{ts} {level} [{record.name}] {msg}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(
    level: str | int = "INFO",
    *,
    json_logs: bool | None = None,
) -> None:
    """Configure the root logger.

    Parameters
    ----------
    level:
        Minimum log level (name or integer constant).
    json_logs:
        Force JSON output when ``True``, plain text when ``False``.
        Defaults to checking the ``LOG_FORMAT`` env-var for ``"json"``.
    """
    if json_logs is None:
        json_logs = os.getenv("LOG_FORMAT", "").lower() == "json"

    # In test runners like pytest, don't wipe out capture handlers
    if "pytest" in sys.modules:
        logging.getLogger().setLevel(level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if json_logs else _TextFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ("slack_sdk", "slack_bolt", "apscheduler", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for *name*, typically ``__name__``."""
    return logging.getLogger(name)
