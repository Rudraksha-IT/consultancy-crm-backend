from __future__ import annotations


class AlreadyAskedError(Exception):
    """The Student already has a Waiting Transfer Request."""


class NotWaitingError(Exception):
    """The request was answered, cancelled or closed already."""


class RequesterCantCounselError(Exception):
    """The person who asked can no longer be a Student's counsellor."""
