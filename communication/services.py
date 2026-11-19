"""Communication's rules: emailing Students through the product's own account (ADR 0023).

Every email is built by the email builder in the office's look (name, logo, Theme colour, Office
details) and sent from the no-reply address with Reply-To set to the office's contact email.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

from django.conf import settings

from communication.exceptions import NoContactEmailError
from communication.models import Channel, Message, MessageStatus
from communication.types import BulkResult, EmailText
from emails.builder import build_email
from emails.exceptions import SendFailedError
from emails.office import office_look
from emails.sender import send_email
from emails.types import BuiltEmail
from emails.types import Message as EmailMessage
from staff.actors import actor_name, stored_staff
from staff.models import Staff
from students.models import Student
from tenancy.models import Consultancy
from tenancy.services import get_office_details

logger = logging.getLogger(__name__)

NO_EMAIL_MESSAGE = "This Student has no email address."
TOO_SLOW_MESSAGE = "Not sent: sending took too long. Send again to this Student."


def _built(consultancy: Consultancy, text: EmailText) -> BuiltEmail:
    """The email in the office's look.

    Raises:
        NoContactEmailError: the office has no contact email yet.
    """
    if get_office_details(consultancy).contact_email == "":
        raise NoContactEmailError
    return build_email(office_look(consultancy), EmailMessage(text.subject, text.body))


def send_to_student(
    student: Student, text: EmailText, by: Staff, consultancy: Consultancy
) -> Message:
    """Email `student` and record it, sent or failed.

    A missing email address, or a mail server that doesn't take it, is recorded as failed with
    its reason.

    Raises:
        NoContactEmailError: the office has no contact email; nothing is recorded.
    """
    return _deliver(student, text, by, _built(consultancy, text))


def send_to_students(
    students: Iterable[Student], text: EmailText, by: Staff, consultancy: Consultancy
) -> BulkResult:
    """Email each of `students` their own copy, one by one, recording each, sent or failed.

    One Student's failure never stops the rest. Students not reached within
    `settings.BULK_SEND_SECONDS` are recorded as failed without being sent to, so the request
    always ends in time.

    Raises:
        NoContactEmailError: the office has no contact email; nothing is recorded.
    """
    email = _built(consultancy, text)
    deadline = time.monotonic() + settings.BULK_SEND_SECONDS
    messages = [
        _deliver(student, text, by, email)
        if time.monotonic() < deadline
        else _record(student, text, by, ("", TOO_SLOW_MESSAGE))
        for student in students
    ]
    result = BulkResult(
        sent=sum(message.status == MessageStatus.SENT for message in messages),
        failed=[message for message in messages if message.status == MessageStatus.FAILED],
    )
    logger.info(
        "Bulk email sent: by_staff_id=%s, sent=%s, failed=%s",
        by.pk,
        result.sent,
        len(result.failed),
    )
    return result


def _deliver(student: Student, text: EmailText, by: Staff, email: BuiltEmail) -> Message:
    """Send `email` to `student` and record it, sent or failed."""
    if student.email == "":
        return _record(student, text, by, ("", NO_EMAIL_MESSAGE))
    try:
        send_email(email, student.email)
    except SendFailedError as failure:
        # silent: the outside call logged it; stored as failed
        return _record(student, text, by, (student.email, failure.reason))
    return _record(student, text, by, (student.email, ""))


def _record(student: Student, text: EmailText, by: Staff, outcome: tuple[str, str]) -> Message:
    """Store one email; `outcome` is where it went and why it failed ("" when sent)."""
    sent_to, error = outcome
    message = Message.objects.create(
        student=student,
        channel=Channel.EMAIL,
        subject=text.subject,
        body=text.body,
        status=MessageStatus.FAILED if error else MessageStatus.SENT,
        error=error,
        sent_to=sent_to,
        sent_by=stored_staff(by),
        sent_by_name=actor_name(by),
    )
    logger.info(
        "Email sent: message_id=%s, student_id=%s, status=%s",
        message.pk,
        student.pk,
        message.status,
    )
    return message
