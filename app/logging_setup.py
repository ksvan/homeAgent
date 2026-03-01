from __future__ import annotations

import logging
from typing import Any


def configure_logging(log_level: str = "INFO", log_format: str = "console") -> None:
    """
    Configure structlog to process both native structlog and stdlib logging calls.

    In development (log_format="console") output is human-readable with colours.
    In production (log_format="json") every line is a JSON object for log aggregators.
    """
    import structlog

    level = getattr(logging, log_level.upper(), logging.INFO)

    # Processors shared by both the structlog chain and the stdlib foreign_pre_chain
    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: Any
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # Native structlog loggers use this chain; the last processor hands off to
    # stdlib's ProcessorFormatter which applies the renderer.
    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )

    # All stdlib loggers (including third-party) are routed through this formatter.
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
