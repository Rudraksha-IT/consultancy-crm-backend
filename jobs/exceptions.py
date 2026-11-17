from __future__ import annotations


class UnsafeJobPayloadError(Exception):
    """A job's payload holds something other than ids and plain values (a programming error)."""


class UnknownJobKindError(Exception):
    """No handler is registered for a job's kind (a programming error)."""
