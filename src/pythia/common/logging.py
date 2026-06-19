"""Observability backbone (§3.10, §8.4, §5.3): structlog JSON logs + correlation IDs.

The single place logging is configured. Pure-core modules (scoring/risk/eval/portfolio) never
import this — it lives at the composition/adapter layer.
"""

import logging as _stdlib_logging
from typing import Literal

import logfire
import structlog
from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
    get_contextvars,
    merge_contextvars,
)
from structlog.typing import EventDict, FilteringBoundLogger, Processor, WrappedLogger

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

_CORRELATION_KEY = "correlation_id"


def _add_service(service: str) -> Processor:
    def processor(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service)
        return event_dict

    return processor


def configure_logging(
    *,
    service: str,
    level: LogLevel = "INFO",
    json_logs: bool = True,
    logfire_token: str | None = None,
    environment: str = "dev",
) -> None:
    logfire.configure(
        token=logfire_token,
        send_to_logfire="if-token-present",
        service_name=service,
        environment=environment,
        console=False,
    )
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            _add_service(service),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            logfire.StructlogProcessor(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            _stdlib_logging.getLevelNamesMapping()[level]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    logger: FilteringBoundLogger = structlog.get_logger(name)
    return logger


def bind_correlation(correlation_id: str, **fields: object) -> None:
    """Start a fresh log scope: clear all contextvars, then bind the correlation id and fields."""
    clear_contextvars()
    bind_contextvars(**{_CORRELATION_KEY: correlation_id}, **fields)


def bind(**fields: object) -> None:
    bind_contextvars(**fields)


def clear_correlation() -> None:
    """Clear all bound contextvars — call at the end of a run scope to prevent context leakage."""
    clear_contextvars()


def current_correlation_id() -> str | None:
    value = get_contextvars().get(_CORRELATION_KEY)
    return value if isinstance(value, str) else None
