"""One way to log a call to an outside service: S3 or the product's email server (issue 58).

Success is one INFO line, failure one ERROR line with the traceback; both name the provider and
the operation and say how long it took. Credentials, message text and recipients are never
passed in, so they can't be logged.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

# The product's own email server (ADR 0023), the only one email ever goes through.
EMAIL_PROVIDER = "email"

logger = logging.getLogger(__name__)
_MS_PER_SECOND = 1000


def _elapsed_ms(started: float) -> int:
    return round((monotonic() - started) * _MS_PER_SECOND)


@contextmanager
def outside_call(provider: str, operation: str) -> Iterator[None]:
    """Log the call made inside the block; an exception is logged and raised again."""
    started = monotonic()
    try:
        yield
    except BaseException:
        logger.exception(
            "outside call failed: provider=%s operation=%s duration_ms=%s",
            provider,
            operation,
            _elapsed_ms(started),
        )
        raise
    logger.info(
        "outside call done: provider=%s operation=%s duration_ms=%s",
        provider,
        operation,
        _elapsed_ms(started),
    )
