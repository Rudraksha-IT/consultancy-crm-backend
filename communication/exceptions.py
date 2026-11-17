from __future__ import annotations


class SendFailedError(Exception):
    """The SMS company or email server didn't send it; `reason` is safe to show and store."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NotSetUpError(Exception):
    """The channel has no account saved yet, so nothing can be sent on it."""


class NoMobileError(Exception):
    """The Staff member has no Nepali mobile number to send a test SMS to."""
