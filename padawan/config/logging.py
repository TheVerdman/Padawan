from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


class _DynamicStderrLoggerFactory:
    """Bind stderr when a log call occurs, not when an embedded CLI configures logging."""

    def __call__(self, *args: Any) -> structlog.PrintLogger:
        del args
        return structlog.PrintLogger(file=sys.stderr)


def configure_logging(level: str = "INFO") -> None:
    """Configure JSON logs without leaking request bodies or secrets."""

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=_DynamicStderrLoggerFactory(),
        cache_logger_on_first_use=False,
    )
