"""SMS companies and email servers, behind one interface each so tests fake them (spec 88-89).

`sms_gateway()` and `email_gateway()` return what the settings name: the real ones here, or the
fakes in tests. What the SMS companies are sent, and how their answers are read, are plain
functions, so they are tested without the network.
"""

from __future__ import annotations

import json
import re
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from email.utils import formataddr
from typing import Protocol

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils.module_loading import import_string

from communication.exceptions import SendFailedError
from communication.models import SmsProvider
from communication.types import EmailAccount, SmsAccount
from config.outside_calls import outside_call

SPARROW_URL = "https://api.sparrowsms.com/v2/sms/"
AAKASH_URL = "https://sms.aakashsms.com/sms/v3/send"
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
SMTP_PROVIDER = "smtp"
# Seconds to wait for an SMS company or an email server before giving up.
SEND_TIMEOUT = 10
HTTP_OK = 200
SSL_PORT = 465
# The most of a company's answer that is read.
ANSWER_MAX_BYTES = 64 * 1024

# Why a send failed, in words safe to show and store: never the company's own text.
SMS_REFUSED = "The SMS company didn't send it. Check the SMS settings in Configuration."
SMS_UNREACHABLE = "Couldn't reach the SMS company. Try again in a few minutes."
EMAIL_REFUSED = "The email server didn't send it. Check the email settings in Configuration."
EMAIL_UNREACHABLE = "Couldn't reach the email server. Try again in a few minutes."

# A Nepali mobile number: 10 digits starting 96, 97 or 98, optionally after +977 or 977.
_NEPALI_MOBILE = re.compile(r"^(?:\+?977)?(9[678][0-9]{8})\Z")
_SEPARATORS = re.compile(r"[ -]")

PostForm = Callable[[str, Mapping[str, str]], tuple[int, bytes]]


class SmsGateway(Protocol):
    """Sends one SMS through the office's SMS company."""

    def send(self, account: SmsAccount, to: str, text: str) -> None:
        """Send `text` to the mobile number `to`.

        Raises:
            SendFailedError: it wasn't sent.
        """


class EmailGateway(Protocol):
    """Sends one email through the office's email server."""

    def send(self, account: EmailAccount, to: str, subject: str, text: str) -> None:
        """Send a plain-text email to `to`.

        Raises:
            SendFailedError: it wasn't sent.
        """


def nepali_mobile(phone: str) -> str | None:
    """`phone` as the 10 digits an SMS company takes, or None when it isn't a Nepali mobile."""
    match = _NEPALI_MOBILE.match(_SEPARATORS.sub("", phone))
    return match.group(1) if match else None


def sms_request(account: SmsAccount, to: str, text: str) -> tuple[str, dict[str, str]]:
    """The address and form fields that ask `account`'s company to send `text` to `to`."""
    if account.provider == SmsProvider.SPARROW:
        return SPARROW_URL, {"token": account.token, "from": account.sender, "to": to, "text": text}
    return AAKASH_URL, {"auth_token": account.token, "to": to, "text": text}


def sms_accepted(provider: str, status: int, body: bytes) -> bool:
    """Whether the company's answer says the SMS was accepted for sending."""
    if status != HTTP_OK:
        return False
    try:
        answer = json.loads(body)
    except ValueError:
        # silent: an answer that isn't JSON means not accepted
        return False
    if not isinstance(answer, dict):
        return False
    if provider == SmsProvider.SPARROW:
        return answer.get("response_code") == HTTP_OK
    return answer.get("error") is False


def post_form(url: str, fields: Mapping[str, str]) -> tuple[int, bytes]:
    """POST `fields` as a form to `url` (one of ours, always https); return status and body."""
    request = urllib.request.Request(  # noqa: S310  # Only SPARROW_URL or AAKASH_URL, https.
        url, data=urllib.parse.urlencode(fields).encode(), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT) as response:  # noqa: S310
            return int(response.status), bytes(response.read(ANSWER_MAX_BYTES))
    except urllib.error.HTTPError as error:
        # silent: an error status is an answer, read below
        return int(error.code), bytes(error.read(ANSWER_MAX_BYTES))


class HttpSmsGateway:
    """Sparrow SMS or Aakash SMS over HTTPS."""

    def __init__(self, post: PostForm = post_form) -> None:
        self._post = post

    def send(self, account: SmsAccount, to: str, text: str) -> None:
        """Ask the company to send it, and check its answer."""
        url, fields = sms_request(account, to, text)
        with outside_call(account.provider, "send_sms"):
            try:
                status, body = self._post(url, fields)
            except OSError as error:
                raise SendFailedError(SMS_UNREACHABLE) from error
            if not sms_accepted(account.provider, status, body):
                raise SendFailedError(SMS_REFUSED)


class SmtpEmailGateway:
    """The office's own email server, always over TLS."""

    def __init__(self, backend: str = SMTP_BACKEND) -> None:
        self._backend = backend

    def send(self, account: EmailAccount, to: str, subject: str, text: str) -> None:
        """Log in to the office's server and send it."""
        is_ssl = account.port == SSL_PORT
        connection = get_connection(
            self._backend,
            fail_silently=False,
            host=account.host,
            port=account.port,
            username=account.username,
            password=account.password,
            use_tls=not is_ssl,
            use_ssl=is_ssl,
            timeout=SEND_TIMEOUT,
        )
        email = EmailMessage(
            subject,
            text,
            formataddr((account.from_name, account.from_address)),
            [to],
            connection=connection,
        )
        with outside_call(SMTP_PROVIDER, "send_email"):
            try:
                email.send()
            except smtplib.SMTPException as error:
                raise SendFailedError(EMAIL_REFUSED) from error
            except OSError as error:
                raise SendFailedError(EMAIL_UNREACHABLE) from error


def sms_gateway() -> SmsGateway:
    """The SMS gateway the settings name (`SMS_GATEWAY`)."""
    gateway: SmsGateway = import_string(settings.SMS_GATEWAY)()
    return gateway


def email_gateway() -> EmailGateway:
    """The email gateway the settings name (`EMAIL_GATEWAY`)."""
    gateway: EmailGateway = import_string(settings.EMAIL_GATEWAY)()
    return gateway
