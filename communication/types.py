"""What Communication's services take and give."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from communication.models import Message


@dataclass(frozen=True)
class SmsAccount:
    """The office's account with its SMS company, the token unsealed."""

    provider: str
    sender: str
    token: str


@dataclass(frozen=True)
class EmailAccount:
    """The office's email account, the password unsealed."""

    host: str
    port: int
    username: str
    password: str
    from_address: str
    from_name: str


@dataclass(frozen=True)
class ProviderDetails:
    """What an Admin saves; a secret of None keeps the saved one."""

    sms_provider: str
    sms_sender: str
    sms_token: str | None
    email_host: str
    email_port: int
    email_username: str
    email_password: str | None
    email_from: str


@dataclass(frozen=True)
class TestSmsResult:
    """How a test SMS went."""

    # Not a pytest test class, despite its name.
    __test__ = False

    status: str
    error: str
    sent_to: str


@dataclass(frozen=True)
class BulkResult:
    """How one message to many Students went: how many were sent, and the failed messages."""

    sent: int
    failed: list[Message]
