"""
Structured JSON logging module for the Enterprise LLM Sentinel.

Usage:
    from logger import setup_logging, get_logger

    setup_logging()                       # Call once at startup
    log = get_logger(__name__)
    log.info("Chat request", extra={"request_id": rid, "provider": p})
"""

import json
import logging
import logging.handlers
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge extra fields (skip internal LogRecord attributes)
        _RESERVED = {
            "name", "msg", "args", "created", "relativeCreated",
            "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "pathname", "filename", "module", "levelno", "levelname",
            "thread", "threadName", "process", "processName",
            "msecs", "message", "taskName",
        }
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _RESERVED:
                continue
            log_entry[key] = value

        # Exception info
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = "".join(
                traceback.format_exception(*record.exc_info)
            )

        if record.stack_info:
            log_entry["stack_info"] = record.stack_info

        return json.dumps(log_entry, default=str, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """Colored console formatter for development."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        base = f"{color}{timestamp} [{record.levelname:>8}] {record.name}: {record.getMessage()}{self.RESET}"

        # Append extra fields if present
        _RESERVED = {
            "name", "msg", "args", "created", "relativeCreated",
            "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "pathname", "filename", "module", "levelno", "levelname",
            "thread", "threadName", "process", "processName",
            "msecs", "message", "taskName",
        }
        extras = {
            k: v for k, v in record.__dict__.items()
            if not k.startswith("_") and k not in _RESERVED
        }
        if extras:
            base += f" | {json.dumps(extras, default=str, ensure_ascii=False)}"

        if record.exc_info and record.exc_info[0] is not None:
            base += "\n" + "".join(traceback.format_exception(*record.exc_info))

        return base


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "text",
    log_file: str | None = None,
) -> None:
    """
    Configure root logger for the application.

    Args:
        log_level: DEBUG | INFO | WARNING | ERROR
        log_format: "json" (production) or "text" (development)
        log_file: Optional file path; file handler always uses JSON format.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove any existing handlers
    root.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(ColoredFormatter())
    root.addHandler(console_handler)

    # File handler (always JSON)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=50 * 1024 * 1024,  # 50 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(JSONFormatter())
        root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "uvicorn.access", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Shorthand for logging.getLogger(name)."""
    return logging.getLogger(name)
