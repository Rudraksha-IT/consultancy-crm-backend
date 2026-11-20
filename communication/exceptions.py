from __future__ import annotations


class NoContactEmailError(Exception):
    """The office has no contact email yet, so nothing can be sent to Students (ADR 0023)."""


class ReadyMadeOffError(Exception):
    """The ready-made email belongs to a switched-off Module (Accounts or Batches): not found."""


class ReadyMadeRefusedError(Exception):
    """This Staff member may not send this ready-made email; `reason` is safe to show."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReadyMadeUnavailableError(Exception):
    """The ready-made email can't be used now (e.g. nothing left to pay); `reason` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RecordNotChosenError(Exception):
    """The record the email is about is missing, not this Student's, or not one to pick."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
