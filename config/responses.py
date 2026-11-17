"""Answers shared by endpoints that must look the same whatever they find."""

from __future__ import annotations

from collections.abc import Callable

from rest_framework.response import Response


class AnswerThenRun(Response):
    """A response that runs `then` once it has been sent.

    The server calls `close()` after writing the body (WSGI), so the work in `then` (looking
    an email up and sending it) can never change the answer or how long it takes. Used by the
    Central login (issue 44) and the Platform's forgot password (issue 63).
    """

    def __init__(self, then: Callable[[], None], data: dict[str, str], status: int) -> None:
        """Answer `data` with `status`, and keep `then` for after the answer."""
        super().__init__(data, status=status)
        self._then = then

    def close(self) -> None:
        """Run `then`, then close as usual (which also releases the database connection)."""
        try:
            self._then()
        finally:
            super().close()
