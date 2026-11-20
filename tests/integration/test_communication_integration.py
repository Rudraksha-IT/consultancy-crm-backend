"""Email to one Student, through the product's own email account (ADR 0023).

Spec (2026-11-18) stories 1-2, 5-10, 13-14, 51; issue 04. Sent email is read from Django's test
mailbox; the broken mail backend (tests/fakes.py) stands in for a mail server that is down.
"""

from __future__ import annotations

from email.utils import parseaddr
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django_tenants.utils import tenant_context
from rest_framework import status

from communication.models import Message
from emails.types import OfficeDetails
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff
from students.models import Student
from tenancy.logo import PNG, SVG, CheckedLogo
from tenancy.services import set_logo, set_office_details
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings
    from rest_framework.response import _MonkeyPatchedResponse

MESSAGES_URL = "/api/messages/"
CONTACT_EMAIL = "info@abcedu.example"
NO_CONTACT_EMAIL = {
    "detail": "Your Admin needs to add the office's contact email in Configuration first.",
    "code": "no_contact_email",
}
SEND_FAILED = "The email couldn't be sent. Try again in a few minutes."
NO_EMAIL_ADDRESS = "This Student has no email address."
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'


def student_messages_url(student: Student | int | str) -> str:
    """One Student's emails."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/messages/"


def send(
    office: ConsultancyContext, student: Student | int | str, body: object
) -> _MonkeyPatchedResponse:
    """POST an email to one Student, as given."""
    return office.client.post(student_messages_url(student), body, format="json")


def email(
    subject: str = "Your visit tomorrow", body: str = "Dear Sita,\n\nSee you at 10."
) -> dict[str, object]:
    """An email's subject line and message."""
    return {"subject": subject, "body": body}


def stored_messages(office: ConsultancyContext) -> list[Message]:
    """Every stored email, oldest first."""
    with tenant_context(office.consultancy):
        return list(Message.objects.order_by("pk"))


def outbox() -> list[EmailMultiAlternatives]:
    """Every email Django's test mailbox received, with its HTML part."""
    sent: list[EmailMultiAlternatives] = []
    for message in mail.outbox:
        assert isinstance(message, EmailMultiAlternatives)
        sent.append(message)
    return sent


def html_of(message: EmailMultiAlternatives) -> str:
    """The email's HTML part."""
    [(html, kind)] = message.alternatives
    assert kind == "text/html"
    return str(html)


def message_answer(message: Message, student: Student, staff: Staff) -> dict[str, Any]:
    """What the API says about one stored email."""
    return {
        "id": str(message.public_id),
        "student": {"id": str(student.public_id), "name": student.name},
        "channel": message.channel,
        "subject": message.subject,
        "body": message.body,
        "status": message.status,
        "error": message.error,
        "sent_to": message.sent_to,
        "attachments": message.attachments,
        "sent_at": message.sent_at.isoformat(),
        "sent_by": {"id": str(staff.public_id), "name": staff.name},
    }


def set_contact_email(office: ConsultancyContext, contact_email: str = CONTACT_EMAIL) -> None:
    """Save the office's details, with `contact_email` (empty: none)."""
    set_office_details(
        office.consultancy,
        OfficeDetails(contact_email=contact_email, phone="01-4412345", address_line1="Putalisadak"),
    )


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Email switched on and a contact email."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.COMMUNICATION})
    set_contact_email(office)
    return office


@pytest.fixture
def admin(abc: ConsultancyContext) -> Staff:
    """The office's Admin, logged in."""
    staff = named_staff(abc, "Asha Admin", Role.ADMIN)
    log_in(abc, staff)
    return staff


@pytest.fixture
def counsellor(abc: ConsultancyContext) -> Staff:
    """A Counsellor."""
    return named_staff(abc, "Ram Counsellor", Role.COUNSELLOR)


@pytest.fixture
def sita(abc: ConsultancyContext, counsellor: Staff) -> Student:
    """Ram's Student, with an email address."""
    return make_student(abc, counsellor, name="Sita Gurung", email="sita@example.com")


class TestSendingToOneStudent:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_admins_counsellors_and_front_desk_send_an_email(
        self, abc: ConsultancyContext, sita: Student, role: Role
    ) -> None:
        sender = abc.log_in_as(role)

        response = send(abc, sita, email())

        assert response.status_code == status.HTTP_201_CREATED
        [message] = stored_messages(abc)
        assert response.json() == message_answer(message, sita, sender)
        assert (message.channel, message.status, message.error, message.sent_to) == (
            "email",
            "sent",
            "",
            "sita@example.com",
        )
        assert (message.subject, message.body) == ("Your visit tomorrow", email()["body"])
        [sent] = outbox()
        assert sent.to == ["sita@example.com"]
        assert sent.subject == "Your visit tomorrow"

    def test_it_goes_from_the_no_reply_address_under_the_offices_name(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, settings: Settings
    ) -> None:
        send(abc, sita, email())

        [sent] = outbox()
        assert parseaddr(sent.from_email) == ("ABC Education", settings.DEFAULT_FROM_EMAIL)
        assert sent.reply_to == [CONTACT_EMAIL]

    def test_it_has_html_and_plain_text_with_the_office_details(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        send(abc, sita, email(body="Dear Sita,\n\nSee you at 10.\nBring your passport."))

        [sent] = outbox()
        assert "See you at 10." in sent.body
        assert "Bring your passport." in sent.body
        assert CONTACT_EMAIL in sent.body
        assert "01-4412345" in sent.body
        html = html_of(sent)
        assert ">Dear Sita,</p>" in html
        assert "See you at 10.<br>Bring your passport." in html
        assert "Putalisadak" in html
        assert "can't receive replies" in html
        assert CONTACT_EMAIL in html

    def test_an_office_name_with_line_breaks_is_one_line_in_the_sender(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        abc.consultancy.name = 'ABC "Best"\r\nBcc: someone@evil.example'
        abc.consultancy.save(update_fields=["name"])

        send(abc, sita, email())

        [sent] = outbox()
        assert "\n" not in sent.from_email
        assert "\r" not in sent.from_email
        name, address = parseaddr(sent.from_email)
        assert name == 'ABC "Best" Bcc: someone@evil.example'
        assert address != "someone@evil.example"
        assert sent.bcc == []
        assert "someone@evil.example" not in sent.message().get("Bcc", "")

    def test_a_png_logo_is_carried_inside_the_email(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        set_logo(abc.consultancy, CheckedLogo(PNG, PNG_BYTES))

        send(abc, sita, email())

        [sent] = outbox()
        assert 'src="cid:logo"' in html_of(sent)
        parts = [part for part in sent.message().walk() if part.get("Content-ID") == "<logo>"]
        assert [part.get_content_type() for part in parts] == ["image/png"]
        assert parts[0].get_payload(decode=True) == PNG_BYTES

    def test_an_svg_logo_becomes_the_office_name(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        set_logo(abc.consultancy, CheckedLogo(SVG, SVG_BYTES))

        send(abc, sita, email())

        [sent] = outbox()
        assert "cid:logo" not in html_of(sent)
        assert "ABC Education" in html_of(sent)
        assert not [part for part in sent.message().walk() if part.get("Content-ID")]

    def test_a_counsellor_can_email_a_student_who_isnt_theirs(
        self, abc: ConsultancyContext, sita: Student
    ) -> None:
        log_in(abc, named_staff(abc, "Gita Other", Role.COUNSELLOR))

        response = send(abc, sita, email())

        assert response.json()["status"] == "sent"

    def test_the_subject_and_text_are_trimmed(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        send(abc, sita, email(subject="  Hello  ", body="  Namaste, \n"))

        [message] = stored_messages(abc)
        assert (message.subject, message.body) == ("Hello", "Namaste,")

    def test_a_student_with_no_email_fails_and_is_recorded(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        hari = make_student(abc, name="Hari Thapa", phone="9841000002")

        response = send(abc, hari, email())

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["status"] == "failed"
        assert response.json()["error"] == NO_EMAIL_ADDRESS
        assert outbox() == []

    def test_a_mail_server_that_fails_is_recorded_without_its_words(
        self,
        abc: ConsultancyContext,
        admin: Staff,
        sita: Student,
        settings: Settings,
        json_logs: JsonLogs,
    ) -> None:
        settings.EMAIL_BACKEND = "tests.fakes.BrokenEmailBackend"

        response = send(abc, sita, email(subject="Private subject", body="Private words"))

        assert response.status_code == status.HTTP_201_CREATED
        answer = response.json()
        assert (answer["status"], answer["error"], answer["sent_to"]) == (
            "failed",
            SEND_FAILED,
            "sita@example.com",
        )
        assert "550" not in str(answer)
        [failure] = json_logs.with_message("outside call failed: provider=email ")
        assert failure["level"] == "ERROR"
        for private in ("sita@example.com", "Private subject", "Private words", CONTACT_EMAIL):
            assert private not in json_logs.text()

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_without_a_contact_email_nothing_is_sent_or_recorded(
        self, abc: ConsultancyContext, sita: Student, role: Role
    ) -> None:
        set_contact_email(abc, "")
        abc.log_in_as(role)

        response = send(abc, sita, email())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NO_CONTACT_EMAIL
        assert stored_messages(abc) == []
        assert outbox() == []

    def test_injection_shaped_text_is_kept_as_typed_and_escaped_in_the_html(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        for text in INJECTION_STRINGS:
            response = send(abc, sita, email(subject=text, body=text))
            assert response.status_code == status.HTTP_201_CREATED
            assert (response.json()["subject"], response.json()["body"]) == (text, text)

        assert [m.body for m in stored_messages(abc)] == INJECTION_STRINGS
        for sent in outbox():
            assert "<script>" not in html_of(sent)
            assert "<img" not in html_of(sent).replace('<img src="cid:logo"', "")

    def test_records_read_as_words(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        send(abc, sita, email())

        with tenant_context(abc.consultancy):
            assert str(Message.objects.select_related("student").get()) == (
                "Email to Sita Gurung: Sent"
            )

    def test_logs_ids_only(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, json_logs: JsonLogs
    ) -> None:
        send(abc, sita, email(subject="Secret subject", body="Namaste secret words"))

        [message] = stored_messages(abc)
        [line] = json_logs.with_message("Email sent")
        assert line["message"] == (
            f"Email sent: message_id={message.pk}, student_id={sita.pk}, status=sent, attachments=0"
        )
        for private in ("Secret subject", "Namaste secret words", "sita@example.com"):
            assert private not in json_logs.text()


class TestRefusedEmails:
    def _refused(self, abc: ConsultancyContext, sita: Student, body: object) -> dict[str, Any]:
        response = send(abc, sita, body)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_messages(abc) == []
        assert outbox() == []
        answer: dict[str, Any] = response.json()
        return answer

    def test_unknown_fields_are_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        answer = self._refused(abc, sita, {**email(), "to": "someone@evil.example"})

        assert answer == {"detail": ["Unknown field."]}

    def test_the_old_channel_field_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        answer = self._refused(abc, sita, {**email(), "channel": "sms"})

        assert answer == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"body": "Hi"}, "subject"),
            ({"subject": "Hi"}, "body"),
            ({"subject": "", "body": "Hi"}, "subject"),
            ({"subject": "   ", "body": "Hi"}, "subject"),
            ({"subject": 1, "body": "Hi"}, "subject"),
            ({"subject": None, "body": "Hi"}, "subject"),
            ({"subject": ["Hi"], "body": "Hi"}, "subject"),
            ({"subject": "x" * 151, "body": "Hi"}, "subject"),
            ({"subject": "Two\nlines", "body": "Hi"}, "subject"),
            ({"subject": "Bcc\r\nHi", "body": "Hi"}, "subject"),
            ({"subject": "bell\x07", "body": "Hi"}, "subject"),
            ({"subject": "Hi", "body": ""}, "body"),
            ({"subject": "Hi", "body": "   "}, "body"),
            ({"subject": "Hi", "body": 42}, "body"),
            ({"subject": "Hi", "body": None}, "body"),
            ({"subject": "Hi", "body": ["Hi"]}, "body"),
            ({"subject": "Hi", "body": "x" * 10_001}, "body"),
            ({"subject": "Hi", "body": "bell\x07"}, "body"),
        ],
    )
    def test_wrong_input_is_refused(
        self,
        abc: ConsultancyContext,
        admin: Staff,
        sita: Student,
        body: dict[str, object],
        field: str,
    ) -> None:
        assert field in self._refused(abc, sita, body)

    def test_the_longest_texts_are_accepted(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = send(abc, sita, email(subject="x" * 150, body="x" * 10_000))

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("body", [["Hi"], "Hi", 7, None])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student, body: object
    ) -> None:
        self._refused(abc, sita, body)

    def test_a_form_body_is_refused(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        response = abc.client.post(student_messages_url(sita), email())

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext, admin: Staff) -> None:
        response = send(abc, 999_999, email())

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestSeeingEmails:
    @pytest.fixture
    def sent(self, abc: ConsultancyContext, admin: Staff, sita: Student) -> Student:
        """Two emails to Sita and one to Hari, oldest first; returns Hari."""
        hari = make_student(abc, name="Hari Thapa", email="hari@example.com")
        send(abc, sita, email(subject="First"))
        send(abc, hari, email(subject="To Hari"))
        send(abc, sita, email(subject="Second"))
        return hari

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_staff_see_a_students_emails_newest_first(
        self, abc: ConsultancyContext, sita: Student, sent: Student, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = abc.client.get(student_messages_url(sita))

        assert response.status_code == status.HTTP_200_OK
        assert [m["subject"] for m in response.json()["results"]] == ["Second", "First"]
        assert response.json()["count"] == 2

    def test_the_email_page_lists_everyones_newest_first(
        self, abc: ConsultancyContext, sent: Student
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(MESSAGES_URL)

        assert response.status_code == status.HTTP_200_OK
        answer = response.json()
        assert [m["subject"] for m in answer["results"]] == ["Second", "To Hari", "First"]
        assert (answer["count"], answer["page"], answer["page_size"]) == (3, 1, 25)
        assert answer["results"][1]["student"] == {"id": str(sent.public_id), "name": "Hari Thapa"}

    def test_a_past_sms_stays_readable(
        self, abc: ConsultancyContext, admin: Staff, sita: Student
    ) -> None:
        with tenant_context(abc.consultancy):
            Message.objects.create(
                student=sita,
                channel="sms",
                body="Namaste, class at 7.",
                status="sent",
                sent_to="9841000001",
                sent_by=admin,
                sent_by_name=admin.name,
            )

        [answer] = abc.client.get(student_messages_url(sita)).json()["results"]

        assert (answer["channel"], answer["subject"], answer["body"]) == (
            "sms",
            "",
            "Namaste, class at 7.",
        )
        with tenant_context(abc.consultancy):
            assert str(Message.objects.select_related("student").get()) == (
                "SMS (no longer available) to Sita Gurung: Sent"
            )

    def test_the_list_pages(self, abc: ConsultancyContext, admin: Staff, sita: Student) -> None:
        for number in range(26):
            send(abc, sita, email(subject=f"Email {number}"))

        second = abc.client.get(MESSAGES_URL, {"page": 2}).json()

        assert [m["subject"] for m in second["results"]] == ["Email 0"]
        assert abc.client.get(MESSAGES_URL, {"page": 3}).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("page", ["0", "two", "-1", "1.5"])
    def test_a_bad_page_is_refused(self, abc: ConsultancyContext, sent: Student, page: str) -> None:
        response = abc.client.get(MESSAGES_URL, {"page": page})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_teachers_see_no_emails_and_send_none(
        self, abc: ConsultancyContext, sita: Student, sent: Student
    ) -> None:
        abc.log_in_as(Role.TEACHER)

        responses = [
            abc.client.get(MESSAGES_URL),
            abc.client.get(student_messages_url(sita)),
            send(abc, sita, email()),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 3
        assert all(r.json() == NOT_FOR_TEACHERS for r in responses)
        assert len(stored_messages(abc)) == 3

    def test_logged_out_is_refused(self, abc: ConsultancyContext, sita: Student) -> None:
        logged_out = abc.new_client()

        responses = [
            logged_out.get(MESSAGES_URL),
            logged_out.get(student_messages_url(sita)),
            logged_out.post(student_messages_url(sita), email(), format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3

    def test_switched_off_they_are_hidden_and_kept(
        self, abc: ConsultancyContext, sita: Student, sent: Student
    ) -> None:
        switch_modules(abc.consultancy, set())

        responses = [
            abc.client.get(MESSAGES_URL),
            abc.client.get(student_messages_url(sita)),
            send(abc, sita, email()),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3
        switch_modules(abc.consultancy, {Module.COMMUNICATION})
        assert abc.client.get(MESSAGES_URL).json()["count"] == 3

    @pytest.mark.parametrize(
        ("url", "method"),
        [
            (MESSAGES_URL, "post"),
            (MESSAGES_URL, "put"),
            (MESSAGES_URL, "delete"),
            ("student", "put"),
            ("student", "patch"),
            ("student", "delete"),
        ],
    )
    def test_only_the_methods_needed_are_allowed(
        self, abc: ConsultancyContext, sita: Student, sent: Student, url: str, method: str
    ) -> None:
        target = student_messages_url(sita) if url == "student" else url

        response = getattr(abc.client, method)(target, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestSmsAndOfficeAccountsAreGone:
    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("get", "/api/communication/settings/"),
            ("put", "/api/communication/settings/"),
            ("post", "/api/communication/settings/test-sms/"),
        ],
    )
    def test_the_old_endpoints_are_not_found(
        self, abc: ConsultancyContext, admin: Staff, method: str, url: str
    ) -> None:
        response = getattr(abc.client, method)(url, {}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
