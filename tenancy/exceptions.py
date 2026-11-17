from __future__ import annotations


class ConsultancyNotResolvedError(Exception):
    """A request reached Consultancy-scoped code without a Consultancy."""


class NoLogoError(Exception):
    """The Consultancy has no office logo to change."""


class StatusRecordIsAppendOnlyError(Exception):
    """Someone tried to change or delete a recorded Consultancy Status change."""


class InvalidStatusChangeError(Exception):
    """A Consultancy Status change that the rules refuse; the message says why, safe to show."""

    def __init__(self, message: str) -> None:
        """Keep the reason, which the API shows as it is."""
        super().__init__(message)
        self.message = message
