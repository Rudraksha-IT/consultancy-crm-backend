"""What Communication's services take and give."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from communication.models import Message


@dataclass(frozen=True)
class EmailText:
    """A subject line and message as Staff send them, trimmed."""

    subject: str
    body: str


@dataclass(frozen=True)
class BulkResult:
    """How one email to many Students went: how many were sent, and the failed ones."""

    sent: int
    failed: list[Message]
