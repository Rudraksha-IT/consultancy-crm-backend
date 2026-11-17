"""Filters and the JSON formatter of the standard log format (ai-backend's `core/utils.py`).

Every line says where it was written, which Consultancy (host) and which request (correlation
ID) it belongs to, and never carries a secret value.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar, Token
from pathlib import Path

from django.db import connection

from config.correlation import get_correlation_id

NOT_AVAILABLE = "N/A"
REDACTED = "[redacted]"
_SECRET_WORDS = "password|code|token|secret|cookie|authorization|access|refresh"  # noqa: S105  # key names
# A key such as `password`, `new_password`, `refresh_token` or `X-Internal-Token`.
_SECRET_KEY = rf"(?:[\w-]*[_-])?(?:{_SECRET_WORDS})(?:[_-][\w-]*)?"
_SECRET_KEY_NAME = re.compile(_SECRET_KEY, re.IGNORECASE)
# The key (optionally quoted) then `:` or `=`, then its value: quoted, `Bearer <x>`, or one word.
_SECRET_PAIR = re.compile(
    rf"""(?P<key>["']?\b{_SECRET_KEY}\b["']?\s*[:=]\s*)"""
    r"""(?P<value>"[^"]*"|'[^']*'|(?:Bearer|Basic)\s+[^\s,;&}\]]+|[^\s,;&}\]]+)""",
    re.IGNORECASE,
)
# Personal data that may sit inside an outside service's error (e.g. a refused recipient).
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A Nepali mobile number, alone or after 977 / +977 (with an optional - or space).
_MOBILE = re.compile(r"(?<![\w-])(?:\+?977[- ]?)?9[678]\d{8}(?![\w-])")
EMAIL_REDACTED = "[email]"
MOBILE_REDACTED = "[phone]"
_STANDARD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({})))
# The host a background job works for (issue 60), for its lines written on the public schema.
_log_tenant: ContextVar[str | None] = ContextVar("log_tenant", default=None)


def set_log_tenant(host: str | None) -> Token[str | None]:
    """Make `host` the `tenant` of lines written outside a Consultancy's schema.

    Pass the returned token to `reset_log_tenant`. Inside a Consultancy, its own host wins.
    """
    return _log_tenant.set(host)


def reset_log_tenant(token: Token[str | None]) -> None:
    """Put back the `tenant` that was current before `set_log_tenant`."""
    _log_tenant.reset(token)


def redact(text: str) -> str:
    """Blank secret-looking `key=value` / `"key": value` values, emails and mobile numbers."""
    text = _SECRET_PAIR.sub(lambda match: f"{match['key']}{REDACTED}", text)
    text = _EMAIL.sub(EMAIL_REDACTED, text)
    return _MOBILE.sub(MOBILE_REDACTED, text)


class DirectoryFilter(logging.Filter):
    """Add `dirname`, the name of the folder holding the file that logged."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Set `record.dirname` and keep the record."""
        record.dirname = Path(record.pathname).parent.name
        return True


class CorrelationFilter(logging.Filter):
    """Add the current request's `correlation_id`, or "N/A" outside a request."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Set `record.correlation_id` and keep the record."""
        record.correlation_id = get_correlation_id() or NOT_AVAILABLE
        return True


class TenantContextFilter(logging.Filter):
    """Add the Consultancy's `schema_name` and `domain_url` (its host), as django-tenants' does.

    Unlike django-tenants' own filter, it never fails before a Consultancy is active, and says
    "N/A" outside a Consultancy (the public schema, e.g. on the Platform host), unless a
    background job has named the host it works for (`set_log_tenant`).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Set `record.schema_name` and `record.domain_url` and keep the record."""
        tenant = getattr(connection, "tenant", None)
        record.schema_name = getattr(tenant, "schema_name", None) or NOT_AVAILABLE
        record.domain_url = (
            getattr(tenant, "domain_url", None) or _log_tenant.get() or NOT_AVAILABLE
        )
        return True


class TracebackOnlyFilter(logging.Filter):
    """Keep only records that carry a traceback (Django's line for an uncaught error)."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Whether the record has exception information."""
        return bool(record.exc_info)


class RedactionFilter(logging.Filter):
    """Blank secret values in the message, its arguments, extra fields and any traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record without secret values and keep it."""
        record.msg = redact(_message(record))
        record.args = None
        for name in vars(record).keys() - _STANDARD_ATTRIBUTES:
            if _SECRET_KEY_NAME.fullmatch(name):
                setattr(record, name, REDACTED)
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def _message(record: logging.LogRecord) -> str:
    """The record's message with its arguments filled in; never raises on a bad log call."""
    try:
        return record.getMessage()
    except (TypeError, ValueError):
        # silent: a bad log call still writes its message
        return str(record.msg)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with ai-backend's keys in ai-backend's order."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a JSON line."""
        directory = getattr(record, "dirname", NOT_AVAILABLE)
        entry: dict[str, object] = {
            "level": record.levelname,
            "timestamp": self.formatTime(record, self.datefmt),
            "source": f"{directory}/{record.filename}: {record.funcName}, line {record.lineno}",
            "tenant": getattr(record, "domain_url", NOT_AVAILABLE),
            "thread": record.threadName,
            "thread_id": record.thread,
            "correlation_id": getattr(record, "correlation_id", NOT_AVAILABLE),
            "message": _message(record),
        }
        if record.exc_info:
            entry["exception"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(entry)
