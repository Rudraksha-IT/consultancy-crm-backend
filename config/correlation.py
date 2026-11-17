"""The correlation ID of the request being handled, kept in a ContextVar (not a thread local).

The Next.js server creates one per call and sends it as `X-Correlation-ID` (issue 43), so its
log entry and Django's lines for the same call can be found together.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

CORRELATION_ID_HEADER = "X-Correlation-ID"
# Only a hyphenated UUID is kept, so the header can never put other text into the logs.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def accept_correlation_id(sent: str | None) -> str:
    """Return the sent ID (lowercased) if it is a UUID, otherwise a new random one."""
    if sent is not None and _UUID.fullmatch(sent):
        return sent.lower()
    return str(uuid.uuid4())


def get_correlation_id() -> str | None:
    """Return the current request's correlation ID, or None outside a request."""
    return _correlation_id.get()


def set_correlation_id(correlation_id: str) -> Token[str | None]:
    """Make `correlation_id` current; pass the returned token to `reset_correlation_id`."""
    return _correlation_id.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    """Put back the correlation ID that was current before `set_correlation_id`."""
    _correlation_id.reset(token)


def clear_correlation_id(**_: object) -> None:
    """Forget the correlation ID; connected to `request_finished`, so it ends with its request."""
    _correlation_id.set(None)
