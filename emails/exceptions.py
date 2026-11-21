"""What can go wrong when an email is sent (ADR 0023)."""

from __future__ import annotations

# Why a send failed, in words safe to show and store: never the mail server's own text.
SEND_FAILED_MESSAGE = "The email couldn't be sent. Try again in a few minutes."


class SendFailedError(Exception):
    """The mail server didn't take the email; `reason` is safe to show and store.

    `cause` names the kind of failure (an exception class) for the log, never the server's words,
    which can hold addresses or anything else it chose to send back.
    """

    def __init__(self, reason: str = SEND_FAILED_MESSAGE, *, cause: str = "") -> None:
        super().__init__(f"{reason} ({cause})" if cause else reason)
        self.reason = reason
