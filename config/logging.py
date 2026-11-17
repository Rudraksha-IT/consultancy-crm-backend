"""The LOGGING setting, in the standard format of our other product (ai-backend, issue 43).

JSON lines on stdout by default; `LOG_FORMAT=console` gives the readable form for local use.
INFO everywhere we deploy; DEBUG only when `LOG_LEVEL=DEBUG` is set locally.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from django.core.exceptions import ImproperlyConfigured

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_FORMATS = ("json", "console")
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "json"
# A request slower than this many milliseconds is logged at WARNING (issue 58).
DEFAULT_SLOW_REQUEST_MS = 1000
MAX_SLOW_REQUEST_MS = 600_000
DATE_FORMAT = "%Y-%m-%d %H:%M:%S %z"
CONSOLE_FORMAT = (
    "[%(levelname)s] (%(asctime)s) [%(dirname)s/%(filename)s: %(funcName)s, line %(lineno)d] "
    "[Tenant: %(domain_url)s, %(threadName)s: %(thread)s, Correlation ID: %(correlation_id)s] "
    "%(message)s"
)
_FILTERS = ["directory_filter", "correlation_filter", "tenant_context", "redaction_filter"]

LogFormat = Literal["json", "console"]


def read_log_level(environ: Mapping[str, str]) -> str:
    """Return `LOG_LEVEL` (any case) as an upper-case level name; INFO when it isn't set.

    Raises:
        ImproperlyConfigured: The level is not one of DEBUG, INFO, WARNING or ERROR.
    """
    level = environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
    if level not in LOG_LEVELS:
        message = f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}."
        raise ImproperlyConfigured(message)
    return level


def read_log_format(environ: Mapping[str, str]) -> LogFormat:
    """Return `LOG_FORMAT`: "json" (the default) or "console".

    Raises:
        ImproperlyConfigured: The format is neither.
    """
    log_format = environ.get("LOG_FORMAT", DEFAULT_LOG_FORMAT)
    if log_format == "console":
        return "console"
    if log_format != "json":
        message = f"LOG_FORMAT must be one of {', '.join(LOG_FORMATS)}."
        raise ImproperlyConfigured(message)
    return "json"


def read_slow_request_ms(environ: Mapping[str, str]) -> int:
    """Return `LOG_SLOW_REQUEST_MS`, a whole number of milliseconds; 1000 when it isn't set.

    Raises:
        ImproperlyConfigured: The value isn't a whole number from 1 to 600000.
    """
    value = environ.get("LOG_SLOW_REQUEST_MS", str(DEFAULT_SLOW_REQUEST_MS))
    if not value.isdigit() or not 1 <= int(value) <= MAX_SLOW_REQUEST_MS:
        message = f"LOG_SLOW_REQUEST_MS must be a whole number from 1 to {MAX_SLOW_REQUEST_MS}."
        raise ImproperlyConfigured(message)
    return int(value)


def _handler(formatter: str) -> dict[str, Any]:
    return {
        "class": "logging.StreamHandler",
        "formatter": formatter,
        "stream": "ext://sys.stdout",
        "filters": _FILTERS,
    }


def build_logging(level: str, log_format: LogFormat) -> dict[str, Any]:
    """Return the dictConfig for the root logger at `level`, writing in `log_format`.

    Django's and gunicorn's own loggers lose their handlers and pass everything to the root, so
    every line (a worker killed for memory too) has the same format and goes to stdout once.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "directory_filter": {"()": "config.logging_utils.DirectoryFilter"},
            "correlation_filter": {"()": "config.logging_utils.CorrelationFilter"},
            "tenant_context": {"()": "config.logging_utils.TenantContextFilter"},
            "redaction_filter": {"()": "config.logging_utils.RedactionFilter"},
            "traceback_only": {"()": "config.logging_utils.TracebackOnlyFilter"},
        },
        "formatters": {
            "json_formatter": {"()": "config.logging_utils.JsonFormatter", "datefmt": DATE_FORMAT},
            "console_formatter": {"format": CONSOLE_FORMAT, "datefmt": DATE_FORMAT},
        },
        "handlers": {
            "json_handler": _handler("json_formatter"),
            "console_handler": _handler("console_formatter"),
        },
        "loggers": {
            "django": {"handlers": [], "level": level, "propagate": True},
            "django.server": {"handlers": [], "level": level, "propagate": True},
            # Only an uncaught error (with its traceback): every answer already has its request
            # line, so Django's own "Bad Request: ..." lines would only repeat it.
            "django.request": {
                "handlers": [],
                "level": "ERROR",
                "propagate": True,
                "filters": ["traceback_only"],
            },
            "gunicorn.error": {"handlers": [], "level": level, "propagate": True},
            # Django logs every request itself; gunicorn's access line would repeat it, with
            # the caller's address, which is never logged (ADR 0004).
            "gunicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
            # SQL with its parameters (personal data) is never logged, even at DEBUG.
            "django.db.backends": {"handlers": [], "level": "INFO", "propagate": True},
        },
        "root": {"handlers": [f"{log_format}_handler"], "level": level},
    }
