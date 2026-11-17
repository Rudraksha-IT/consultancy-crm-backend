"""Fakes for outside services, swapped in at their interface (spec Testing Decisions)."""

from __future__ import annotations

import smtplib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import IO, ClassVar
from urllib.parse import quote

from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend

from communication.exceptions import SendFailedError
from communication.types import EmailAccount, SmsAccount
from documents.exceptions import DocumentStorageError
from documents.storage import SIGNED_LINK_SECONDS


@dataclass(frozen=True)
class StoredFile:
    """What the fake storage holds for one key."""

    content: bytes
    content_type: str


class FakeDocumentStorage:
    """Document storage in memory. Every instance shares the files, cleared before each test."""

    files: ClassVar[dict[str, StoredFile]] = {}
    is_down: ClassVar[bool] = False

    def save(self, key: str, content: IO[bytes], content_type: str) -> None:
        """Keep the bytes, or fail like an unreachable S3."""
        if self.is_down:
            raise DocumentStorageError
        content.seek(0)
        self.files[key] = StoredFile(content.read(), content_type)

    def signed_link(
        self, key: str, *, file_name: str, content_type: str, as_attachment: bool
    ) -> str:
        """A recognisable link naming everything it was asked for."""
        disposition = "attachment" if as_attachment else "inline"
        return (
            f"https://files.test/{key}?disposition={disposition}"
            f"&name={quote(file_name, safe='')}&type={quote(content_type, safe='')}"
            f"&expires={SIGNED_LINK_SECONDS}"
        )

    def read(self, key: str) -> bytes:
        """The kept bytes, or fail like an unreachable S3 or a missing key."""
        if self.is_down or key not in self.files:
            raise DocumentStorageError
        return self.files[key].content

    def delete(self, key: str) -> None:
        """Forget the bytes, or fail like an unreachable S3."""
        if self.is_down:
            raise DocumentStorageError
        self.files.pop(key, None)


# Why the fake SMS company refuses one of its `refused_numbers`.
SMS_REFUSED_FOR_NUMBER = "The SMS company refused this number."


@dataclass(frozen=True)
class SentSms:
    """One SMS the fake SMS company was asked to send."""

    account: SmsAccount
    to: str
    text: str


@dataclass(frozen=True)
class SentEmail:
    """One email the fake email server was asked to send."""

    account: EmailAccount
    to: str
    subject: str
    text: str


class FakeSmsGateway:
    """An SMS company in memory: it keeps what it's asked to send, or fails with `failure`.

    `refused_numbers` fail with `SMS_REFUSED_FOR_NUMBER`, so one Student's SMS can fail alone.
    """

    sent: ClassVar[list[SentSms]] = []
    failure: ClassVar[str | None] = None
    refused_numbers: ClassVar[set[str]] = set()

    def send(self, account: SmsAccount, to: str, text: str) -> None:
        """Keep the SMS, or fail as told."""
        if self.failure is not None:
            raise SendFailedError(self.failure)
        if to in self.refused_numbers:
            raise SendFailedError(SMS_REFUSED_FOR_NUMBER)
        self.sent.append(SentSms(account, to, text))


class FakeEmailGateway:
    """An email server in memory: it keeps what it's asked to send, or fails with `failure`."""

    sent: ClassVar[list[SentEmail]] = []
    failure: ClassVar[str | None] = None

    def send(self, account: EmailAccount, to: str, subject: str, text: str) -> None:
        """Keep the email, or fail as told."""
        if self.failure is not None:
            raise SendFailedError(self.failure)
        self.sent.append(SentEmail(account, to, subject, text))


class BrokenEmailBackend(BaseEmailBackend):
    """A mail server that is down: every send fails, naming the address as SMTP errors do."""

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        """Fail the way a refused recipient does, with the address in the error."""
        recipients = [address for message in email_messages for address in message.to]
        raise smtplib.SMTPRecipientsRefused(dict.fromkeys(recipients, (550, b"refused")))
