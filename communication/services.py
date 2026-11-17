"""Communication's rules: saving the office's accounts and sending to Students (spec 88-91)."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from communication.exceptions import NoMobileError, NotSetUpError, SendFailedError
from communication.models import (
    SETTINGS_ROW,
    Channel,
    Message,
    MessageStatus,
    ProviderSettings,
    SmsProvider,
)
from communication.providers import email_gateway, nepali_mobile, sms_gateway
from communication.sealing import seal, unseal
from communication.types import (
    BulkResult,
    EmailAccount,
    ProviderDetails,
    SmsAccount,
    TestSmsResult,
)
from staff.actors import actor_name, stored_staff
from staff.models import Staff
from students.models import Student

logger = logging.getLogger(__name__)

NOT_A_MOBILE_MESSAGE = "{phone} isn't a 10-digit Nepali mobile number."
NO_EMAIL_MESSAGE = "This Student has no email address."
TOO_SLOW_MESSAGE = "Not sent: sending took too long. Send again to this Student."


def saved_settings() -> ProviderSettings | None:
    """The office's saved SMS and email settings, if an Admin has saved any."""
    return ProviderSettings.objects.filter(pk=SETTINGS_ROW).first()


def sms_is_ready(saved: ProviderSettings | None) -> bool:
    """Whether SMS can be sent: a company, its token, and Sparrow's sender name."""
    if saved is None or saved.sms_provider == "" or saved.sms_token_sealed == "":
        return False
    return saved.sms_provider != SmsProvider.SPARROW or saved.sms_sender != ""


def email_is_ready(saved: ProviderSettings | None) -> bool:
    """Whether email can be sent: a server, its login and the address mail comes from."""
    return (
        saved is not None
        and saved.email_host != ""
        and saved.email_username != ""
        and saved.email_password_sealed != ""
        and saved.email_from != ""
    )


def save_settings(details: ProviderDetails, by: Staff) -> ProviderSettings:
    """Store the office's accounts; a secret of None keeps the one saved before."""
    with transaction.atomic():
        saved = ProviderSettings.objects.select_for_update().filter(pk=SETTINGS_ROW).first()
        row = saved or ProviderSettings(pk=SETTINGS_ROW)
        row.sms_provider = details.sms_provider
        row.sms_sender = details.sms_sender
        row.email_host = details.email_host
        row.email_port = details.email_port
        row.email_username = details.email_username
        row.email_from = details.email_from
        if details.sms_token is not None:
            row.sms_token_sealed = seal(details.sms_token)
        if details.email_password is not None:
            row.email_password_sealed = seal(details.email_password)
        row.updated_by = stored_staff(by)
        row.updated_at = timezone.now()
        row.save()
    return row


def _sms_account() -> SmsAccount:
    saved = saved_settings()
    if saved is None or not sms_is_ready(saved):
        raise NotSetUpError
    return SmsAccount(saved.sms_provider, saved.sms_sender, unseal(saved.sms_token_sealed))


def _email_account(office_name: str) -> EmailAccount:
    saved = saved_settings()
    if saved is None or not email_is_ready(saved):
        raise NotSetUpError
    return EmailAccount(
        host=saved.email_host,
        port=saved.email_port,
        username=saved.email_username,
        password=unseal(saved.email_password_sealed),
        from_address=saved.email_from,
        from_name=office_name,
    )


def send_to_student(
    student: Student, channel: Channel, body: str, by: Staff, office_name: str
) -> Message:
    """Send `body` to `student` and record it, sent or failed (spec 89, 91).

    A number that isn't a Nepali mobile, a missing email address, or a refusal by the company is
    recorded as a failed message with its reason.

    Raises:
        NotSetUpError: the channel isn't set up; nothing is recorded.
    """
    return _deliver(student, channel, body, by, _account(channel, office_name))


def send_to_students(
    students: Iterable[Student], channel: Channel, body: str, by: Staff, office_name: str
) -> BulkResult:
    """Send `body` to each of `students` one by one, recording each, sent or failed (spec 90-91).

    One Student's failure never stops the rest. Students not reached within
    `settings.BULK_SEND_SECONDS` are recorded as failed without being sent to, so the request
    always ends in time.

    Raises:
        NotSetUpError: the channel isn't set up; nothing is recorded.
    """
    account = _account(channel, office_name)
    deadline = time.monotonic() + settings.BULK_SEND_SECONDS
    messages = [
        _deliver(student, channel, body, by, account)
        if time.monotonic() < deadline
        else _record(student, channel, body, by, ("", TOO_SLOW_MESSAGE))
        for student in students
    ]
    result = BulkResult(
        sent=sum(message.status == MessageStatus.SENT for message in messages),
        failed=[message for message in messages if message.status == MessageStatus.FAILED],
    )
    logger.info(
        "Bulk message sent: by_staff_id=%s, channel=%s, sent=%s, failed=%s",
        by.pk,
        channel.value,
        result.sent,
        len(result.failed),
    )
    return result


def _account(channel: Channel, office_name: str) -> SmsAccount | EmailAccount:
    """The office's account for `channel`.

    Raises:
        NotSetUpError: the channel isn't set up.
    """
    return _sms_account() if channel == Channel.SMS else _email_account(office_name)


def _deliver(
    student: Student,
    channel: Channel,
    body: str,
    by: Staff,
    account: SmsAccount | EmailAccount,
) -> Message:
    """Send `body` to `student` on `account` and record it, sent or failed."""
    if isinstance(account, SmsAccount):
        outcome = _send_sms(student, body, account)
    else:
        outcome = _send_email(student, body, account)
    return _record(student, channel, body, by, outcome)


def _record(
    student: Student, channel: Channel, body: str, by: Staff, outcome: tuple[str, str]
) -> Message:
    """Store one message; `outcome` is where it went and why it failed ("" when sent)."""
    sent_to, error = outcome
    message = Message.objects.create(
        student=student,
        channel=channel,
        body=body,
        status=MessageStatus.FAILED if error else MessageStatus.SENT,
        error=error,
        sent_to=sent_to,
        sent_by=stored_staff(by),
        sent_by_name=actor_name(by),
    )
    logger.info(
        "Message sent: message_id=%s, student_id=%s, channel=%s, status=%s",
        message.pk,
        student.pk,
        channel.value,
        message.status,
    )
    return message


def _send_sms(student: Student, body: str, account: SmsAccount) -> tuple[str, str]:
    """Send the SMS; return the number it went to and why it failed ("" when sent)."""
    to = nepali_mobile(student.phone)
    if to is None:
        return student.phone, NOT_A_MOBILE_MESSAGE.format(phone=student.phone)
    try:
        sms_gateway().send(account, to, body)
    except SendFailedError as failure:
        # silent: the provider call logged it; stored as failed
        return to, failure.reason
    return to, ""


def _send_email(student: Student, body: str, account: EmailAccount) -> tuple[str, str]:
    """Send the email; return the address it went to and why it failed ("" when sent)."""
    if student.email == "":
        return "", NO_EMAIL_MESSAGE
    try:
        email_gateway().send(account, student.email, f"Message from {account.from_name}", body)
    except SendFailedError as failure:
        # silent: the provider call logged it; stored as failed
        return student.email, failure.reason
    return student.email, ""


def send_test_sms(staff: Staff, office_name: str) -> TestSmsResult:
    """Send a test SMS to `staff`'s own mobile, to check the SMS settings. It isn't recorded.

    Raises:
        NotSetUpError: SMS isn't set up.
        NoMobileError: `staff` has no Nepali mobile number.
    """
    account = _sms_account()
    to = nepali_mobile(staff.mobile)
    if to is None:
        raise NoMobileError
    text = f"This is a test SMS from {office_name}. SMS is set up."
    try:
        sms_gateway().send(account, to, text)
    except SendFailedError as failure:
        # silent: the provider call logged it; stored as failed
        result = TestSmsResult(MessageStatus.FAILED.value, failure.reason, to)
    else:
        result = TestSmsResult(MessageStatus.SENT.value, "", to)
    logger.info("Test SMS sent: staff_id=%s, status=%s", staff.pk, result.status)
    return result
