"""
Structured logging for Cosmo.
Uses structlog for JSON output to rotating files + colored console output.
All robot modules use: from utils.logger import get_logger; log = get_logger(__name__)
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import structlog

LOG_DIR = Path.home() / ".robot" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    # stdlib handler → rotating file (JSON)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "cosmo.log",
        maxBytes=10 * 1024 * 1024,   # 10MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)

    # stdlib handler → stderr (human-readable)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(message)s",
        handlers=[file_handler, console_handler],
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # File gets JSON, console gets colored key=value
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # File formatter: JSON
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
    )
    file_handler.setFormatter(file_formatter)

    # Console formatter: colored dev output
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
    )
    console_handler.setFormatter(console_formatter)

    _CONFIGURED = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    _configure()
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """Bind key-value pairs to all log records in the current async context."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
