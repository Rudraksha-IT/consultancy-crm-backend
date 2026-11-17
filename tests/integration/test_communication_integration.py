"""Communication: the office's own SMS and email accounts, and messages to one Student.

Spec 88-89, 91-92; issue 25. The SMS company and the email server are fakes (tests/fakes.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from communication.models import Message, ProviderSettings
from communication.providers import EMAIL_REFUSED, SMS_REFUSED
from communication.sealing import unseal
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff
from students.models import Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.fakes import FakeEmailGateway, FakeSmsGateway
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

SETTINGS_URL = "/api/communication/settings/"
TEST_SMS_URL = "/api/communication/settings/test-sms/"
MESSAGES_URL = "/api/messages/"
SMS_TOKEN = "v2_sparrow-secret-token-7Kq"  # noqa: S105  # test-only credential
EMAIL_PASSWORD = "zoho-app-password-123"  # noqa: S105  # test-only credential
SETTINGS = {
    "sms_provider": "sparrow",
    "sms_sender": "ABCEDU",
    "sms_token": SMS_TOKEN,
    "email_host": "smtp.zoho.com",
    "email_port": 587,
    "email_username": "info@abcedu.example",
    "email_password": EMAIL_PASSWORD,
    "email_from": "info@abcedu.example",
}
SAVED_ANSWER = {
    "sms_provider": "sparrow",
    "sms_sender": "ABCEDU",
    "sms_token_saved": True,
    "email_host": "smtp.zoho.com",
    "email_port": 587,
    "email_username": "info@abcedu.example",
    "email_password_saved": True,
    "email_from": "info@abcedu.example",
    "sms_ready": True,
    "email_ready": True,
}
EMPTY_ANSWER = {
    "sms_provider": "",
    "sms_sender": "",
    "sms_token_saved": False,
    "email_host": "",
    "email_port": 587,
    "email_username": "",
    "email_password_saved": False,
    "email_from": "",
    "sms_ready": False,
    "email_ready": False,
}
ONLY_ADMIN = {"detail": "Only an Admin can change the Configuration."}
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
SMS_NOT_SET_UP = {"channel": ["SMS isn't set up yet. An Admin can set it up in Configuration."]}
EMAIL_NOT_SET_UP = {"channel": ["Email isn't set up yet. An Admin can set it up in Configuration."]}
SENT = {"status": "sent"}


def student_messages_url(student: Student | int) -> str:
    """One Student's messages."""
    pk = student if isinstance(student, int) else student.pk
    return f"/api/students/{pk}/messages/"


def save_settings(office: ConsultancyContext, body: object) -> _MonkeyPatchedResponse:
    """PUT the provider settings, as given."""
    return office.client.put(SETTINGS_URL, body, format="json")


def send(
    office: ConsultancyContext, student: Student | int, body: object
) -> _MonkeyPatchedResponse:
    """POST a message to one Student, as given."""
    return office.client.post(student_messages_url(student), body, format="json")


def stored_settings(office: ConsultancyContext) -> ProviderSettings | None:
    """The stored provider settings, if any."""
    with tenant_context(office.consultancy):
        return ProviderSettings.objects.first()


def stored_messages(office: ConsultancyContext) -> list[Message]:
    """Every stored message, oldest first."""
    with tenant_context(office.consultancy):
        return list(Message.objects.order_by("pk"))


def message_answer(message: Message, student: Student, staff: Staff) -> dict[str, Any]:
    """What the API says about one stored message."""
    return {
        "id": message.pk,
        "student": {"id": student.pk, "name": student.name},
        "channel": message.channel,
        "body": message.body,
        "status": message.status,
        "error": message.error,
        "sent_to": message.sent_to,
        "sent_at": message.sent_at.isoformat(),
        "sent_by": {"id": staff.pk, "name": staff.name},
    }


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with SMS and email switched on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.COMMUNICATION})
    return office


@pytest.fixture
def admin(abc: ConsultancyContext) -> Staff:
    """The office's Admin, with a mobile number."""
    staff = named_staff(abc, "Asha Admin", Role.ADMIN)
    with tenant_context(abc.consultancy):
        staff.mobile = "9851000009"
        staff.save(update_fields=["mobile"])
    return staff


@pytest.fixture
def counsellor(abc: ConsultancyContext) -> Staff:
    """A Counsellor."""
    return named_staff(abc, "Ram Counsellor", Role.COUNSELLOR)


@pytest.fixture
def sita(abc: ConsultancyContext, counsellor: Staff) -> Student:
    """Ram's Student, with a Nepali mobile and an email."""
    return make_student(abc, counsellor, name="Sita Gurung", email="sita@example.com")


@pytest.fixture
def set_up(abc: ConsultancyContext, admin: Staff) -> None:
    """The Admin has saved the SMS and email accounts."""
    log_in(abc, admin)
    assert save_settings(abc, SETTINGS).status_code == status.HTTP_200_OK


class TestProviderSettings:
    def test_nothing_is_set_up_at_first(self, abc: ConsultancyContext, admin: Staff) -> None:
        log_in(abc, admin)

        response = abc.client.get(SETTINGS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == EMPTY_ANSWER

    def test_the_admin_saves_them_and_secrets_are_never_given_back(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        log_in(abc, admin)

        response = save_settings(abc, SETTINGS)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == SAVED_ANSWER
        assert abc.client.get(SETTINGS_URL).json() == SAVED_ANSWER
        assert SMS_TOKEN not in response.content.decode()
        assert EMAIL_PASSWORD not in response.content.decode()

    def test_secrets_are_stored_sealed(self, abc: ConsultancyContext, admin: Staff) -> None:
        log_in(abc, admin)

        save_settings(abc, SETTINGS)

        saved = stored_settings(abc)
        assert saved is not None
        assert SMS_TOKEN not in saved.sms_token_sealed
        assert EMAIL_PASSWORD not in saved.email_password_sealed
        assert unseal(saved.sms_token_sealed) == SMS_TOKEN
        assert unseal(saved.email_password_sealed) == EMAIL_PASSWORD
        assert saved.updated_by_id == admin.pk

    @pytest.mark.parametrize("left_out", ["omitted", "blank"])
    def test_saving_again_without_the_secrets_keeps_them(
        self, abc: ConsultancyContext, set_up: None, left_out: str
    ) -> None:
        body: dict[str, object] = {**SETTINGS, "sms_sender": "ABCNEW"}
        if left_out == "omitted":
            del body["sms_token"], body["email_password"]
        else:
            body.update(sms_token="", email_password="")

        response = save_settings(abc, body)

        assert response.json() == {**SAVED_ANSWER, "sms_sender": "ABCNEW"}
        saved = stored_settings(abc)
        assert saved is not None
        assert unseal(saved.sms_token_sealed) == SMS_TOKEN
        assert unseal(saved.email_password_sealed) == EMAIL_PASSWORD

    def test_a_new_secret_replaces_the_old(self, abc: ConsultancyContext, set_up: None) -> None:
        save_settings(abc, {**SETTINGS, "sms_token": "a-brand-new-token"})

        saved = stored_settings(abc)
        assert saved is not None
        assert unseal(saved.sms_token_sealed) == "a-brand-new-token"

    def test_aakash_needs_no_sender(self, abc: ConsultancyContext, admin: Staff) -> None:
        log_in(abc, admin)

        response = save_settings(abc, {**SETTINGS, "sms_provider": "aakash", "sms_sender": ""})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["sms_ready"] is True

    def test_each_channel_can_be_left_off(self, abc: ConsultancyContext, admin: Staff) -> None:
        log_in(abc, admin)
        body = {
            **SETTINGS,
            "sms_provider": "",
            "sms_sender": "",
            "sms_token": "",
            "email_host": "",
            "email_username": "",
            "email_password": "",
            "email_from": "",
        }

        response = save_settings(abc, body)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == EMPTY_ANSWER

    def test_switching_sms_off_keeps_the_token_for_later(
        self, abc: ConsultancyContext, set_up: None
    ) -> None:
        save_settings(abc, {**SETTINGS, "sms_provider": "", "sms_token": ""})

        response = save_settings(abc, {**SETTINGS, "sms_token": ""})

        assert response.json()["sms_ready"] is True

    def test_logs_the_change_with_ids_and_no_secrets(
        self, abc: ConsultancyContext, admin: Staff, json_logs: JsonLogs
    ) -> None:
        log_in(abc, admin)

        save_settings(abc, SETTINGS)

        [line] = json_logs.with_message("Provider settings saved")
        assert line["message"] == (
            f"Provider settings saved: consultancy_id={abc.consultancy.pk}, staff_id={admin.pk}, "
            "sms_ready=True, email_ready=True"
        )
        for secret in (SMS_TOKEN, EMAIL_PASSWORD, "info@abcedu.example"):
            assert secret not in json_logs.text()


class TestRefusedSettings:
    @pytest.fixture(autouse=True)
    def logged_in(self, abc: ConsultancyContext, admin: Staff) -> None:
        log_in(abc, admin)

    def _refused(self, abc: ConsultancyContext, body: object) -> dict[str, Any]:
        response = save_settings(abc, body)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_settings(abc) is None
        answer: dict[str, Any] = response.json()
        return answer

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        answer = self._refused(abc, {**SETTINGS, "api_secret": "x", "is_admin": True})

        assert answer == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize(
        "field",
        ["sms_provider", "sms_sender", "email_host", "email_port", "email_username", "email_from"],
    )
    def test_every_field_but_the_secrets_is_needed(
        self, abc: ConsultancyContext, field: str
    ) -> None:
        body = {key: value for key, value in SETTINGS.items() if key != field}

        assert field in self._refused(abc, body)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("sms_provider", 1),
            ("sms_provider", "twilio"),
            ("sms_provider", None),
            ("sms_sender", 12345),
            ("sms_sender", "TOO-LONG-NAME"),
            ("sms_sender", "ABC<b>"),
            ("sms_token", 123456789),
            ("sms_token", "has spaces in it"),
            ("sms_token", "x" * 201),
            ("sms_token", "short"),
            ("email_host", "127.0.0.1"),
            ("email_host", "169.254.169.254"),
            ("email_host", "localhost"),
            ("email_host", "postgres"),
            ("email_host", "smtp.zoho.com:25"),
            ("email_host", "smtp zoho.com"),
            ("email_host", "a" * 250 + ".com"),
            ("email_host", ["smtp.zoho.com"]),
            ("email_port", "587"),
            ("email_port", 22),
            ("email_port", 0),
            ("email_port", 65536),
            ("email_port", True),
            ("email_port", 587.5),
            ("email_username", 7),
            ("email_username", "x" * 255),
            ("email_username", "line\nbreak"),
            ("email_password", 12345),
            ("email_password", "x" * 201),
            ("email_password", "tab\tinside"),
            ("email_from", "not-an-email"),
            ("email_from", "info@abcedu.example\nBcc: all@example.com"),
            ("email_from", True),
        ],
    )
    def test_wrong_values_are_refused(
        self, abc: ConsultancyContext, field: str, value: object
    ) -> None:
        assert field in self._refused(abc, {**SETTINGS, field: value})

    @pytest.mark.parametrize("value", INJECTION_STRINGS)
    @pytest.mark.parametrize("field", ["sms_sender", "email_host", "email_from"])
    def test_injection_shaped_text_is_refused(
        self, abc: ConsultancyContext, field: str, value: str
    ) -> None:
        assert field in self._refused(abc, {**SETTINGS, field: value})

    def test_sparrow_needs_a_sender(self, abc: ConsultancyContext) -> None:
        answer = self._refused(abc, {**SETTINGS, "sms_sender": ""})

        assert answer == {"sms_sender": ["Enter the sender name Sparrow SMS approved for you."]}

    def test_sms_needs_a_token_when_none_is_saved(self, abc: ConsultancyContext) -> None:
        answer = self._refused(abc, {**SETTINGS, "sms_token": ""})

        assert answer == {"sms_token": ["Enter the API token from your SMS company."]}

    @pytest.mark.parametrize(
        ("field", "message"),
        [
            ("email_username", "Enter the email account's username."),
            ("email_from", "Enter the address emails are sent from."),
            ("email_password", "Enter the email account's password."),
        ],
    )
    def test_email_needs_its_account_when_a_server_is_given(
        self, abc: ConsultancyContext, field: str, message: str
    ) -> None:
        answer = self._refused(abc, {**SETTINGS, field: ""})

        assert answer == {field: [message]}

    @pytest.mark.parametrize("body", [[SETTINGS], "settings", 7, None])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        response = save_settings(abc, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_settings(abc) is None

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        response = abc.client.put(SETTINGS_URL, SETTINGS)

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        assert stored_settings(abc) is None


class TestSettingsAccess:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_an_admin_sees_or_changes_them(
        self, abc: ConsultancyContext, set_up: None, role: Role
    ) -> None:
        abc.log_in_as(role)

        responses = [
            abc.client.get(SETTINGS_URL),
            save_settings(abc, {**SETTINGS, "sms_sender": "OTHER"}),
            abc.client.post(TEST_SMS_URL, {}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 3
        assert all(r.json() == ONLY_ADMIN for r in responses)
        saved = stored_settings(abc)
        assert saved is not None
        assert saved.sms_sender == "ABCEDU"
        assert FakeSmsGateway.sent == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        logged_out = abc.new_client()

        responses = [
            logged_out.get(SETTINGS_URL),
            logged_out.put(SETTINGS_URL, SETTINGS, format="json"),
            logged_out.post(TEST_SMS_URL, {}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3

    def test_switched_off_they_are_not_found_and_come_back_when_on(
        self, abc: ConsultancyContext, set_up: None
    ) -> None:
        switch_modules(abc.consultancy, set())

        responses = [
            abc.client.get(SETTINGS_URL),
            save_settings(abc, {**SETTINGS, "sms_sender": "OTHER"}),
            abc.client.post(TEST_SMS_URL, {}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3
        switch_modules(abc.consultancy, {Module.COMMUNICATION})
        assert abc.client.get(SETTINGS_URL).json() == SAVED_ANSWER

    @pytest.mark.parametrize(
        ("url", "method"),
        [
            (SETTINGS_URL, "post"),
            (SETTINGS_URL, "patch"),
            (SETTINGS_URL, "delete"),
            (TEST_SMS_URL, "get"),
            (TEST_SMS_URL, "put"),
            (TEST_SMS_URL, "delete"),
        ],
    )
    def test_only_the_methods_needed_are_allowed(
        self, abc: ConsultancyContext, admin: Staff, url: str, method: str
    ) -> None:
        log_in(abc, admin)

        response = getattr(abc.client, method)(url, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestTestSms:
    def test_it_goes_to_the_admins_own_mobile(self, abc: ConsultancyContext, set_up: None) -> None:
        response = abc.client.post(TEST_SMS_URL, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "sent", "error": "", "sent_to": "9851000009"}
        [sms] = FakeSmsGateway.sent
        assert (sms.to, sms.account.token, sms.account.sender) == (
            "9851000009",
            SMS_TOKEN,
            "ABCEDU",
        )
        assert sms.text == "This is a test SMS from ABC Education. SMS is set up."
        assert stored_messages(abc) == []

    def test_a_refusal_is_told(self, abc: ConsultancyContext, set_up: None) -> None:
        FakeSmsGateway.failure = SMS_REFUSED

        response = abc.client.post(TEST_SMS_URL, {}, format="json")

        assert response.json() == {
            "status": "failed",
            "error": SMS_REFUSED,
            "sent_to": "9851000009",
        }

    def test_an_admin_with_no_nepali_mobile_is_told(
        self, abc: ConsultancyContext, set_up: None, admin: Staff
    ) -> None:
        with tenant_context(abc.consultancy):
            admin.mobile = ""
            admin.save(update_fields=["mobile"])

        response = abc.client.post(TEST_SMS_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "Add your Nepali mobile number in your Settings first."
        }
        assert FakeSmsGateway.sent == []

    def test_it_needs_sms_set_up(self, abc: ConsultancyContext, admin: Staff) -> None:
        log_in(abc, admin)

        response = abc.client.post(TEST_SMS_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == SMS_NOT_SET_UP

    def test_it_takes_no_fields(self, abc: ConsultancyContext, set_up: None) -> None:
        response = abc.client.post(TEST_SMS_URL, {"to": "9800000000"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert FakeSmsGateway.sent == []


class TestSendingToOneStudent:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_admins_counsellors_and_front_desk_send_an_sms(
        self, abc: ConsultancyContext, set_up: None, sita: Student, role: Role
    ) -> None:
        sender = abc.log_in_as(role)

        response = send(abc, sita, {"channel": "sms", "body": "Namaste Sita, see you at 10."})

        assert response.status_code == status.HTTP_201_CREATED
        [message] = stored_messages(abc)
        assert response.json() == message_answer(message, sita, sender)
        assert (message.channel, message.status, message.error, message.sent_to) == (
            "sms",
            "sent",
            "",
            "9841000001",
        )
        [sms] = FakeSmsGateway.sent
        assert (sms.to, sms.text, sms.account.token) == (
            "9841000001",
            "Namaste Sita, see you at 10.",
            SMS_TOKEN,
        )

    def test_a_counsellor_can_message_a_student_who_isnt_theirs(
        self, abc: ConsultancyContext, set_up: None, sita: Student
    ) -> None:
        log_in(abc, named_staff(abc, "Gita Other", Role.COUNSELLOR))

        response = send(abc, sita, {"channel": "sms", "body": "Namaste"})

        assert response.json()["status"] == "sent"

    def test_an_email_goes_from_the_offices_account(
        self, abc: ConsultancyContext, set_up: None, sita: Student
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = send(abc, sita, {"channel": "email", "body": "Namaste Sita,\n\nWelcome."})

        assert response.json()["status"] == "sent"
        assert response.json()["sent_to"] == "sita@example.com"
        [email] = FakeEmailGateway.sent
        assert (email.to, email.subject, email.text) == (
            "sita@example.com",
            "Message from ABC Education",
            "Namaste Sita,\n\nWelcome.",
        )
        assert email.account.password == EMAIL_PASSWORD
        assert (email.account.from_address, email.account.from_name) == (
            "info@abcedu.example",
            "ABC Education",
        )

    def test_the_text_is_trimmed(
        self, abc: ConsultancyContext, set_up: None, sita: Student
    ) -> None:
        send(abc, sita, {"channel": "sms", "body": "  Namaste, \n"})

        assert FakeSmsGateway.sent[0].text == "Namaste,"

    def test_a_number_that_isnt_a_nepali_mobile_fails_and_is_recorded(
        self, abc: ConsultancyContext, set_up: None
    ) -> None:
        manisha = make_student(abc, name="Manisha Rai", phone="014412345")

        response = send(abc, manisha, {"channel": "sms", "body": "Namaste"})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["status"] == "failed"
        assert response.json()["error"] == "014412345 isn't a 10-digit Nepali mobile number."
        assert FakeSmsGateway.sent == []
        assert [m.status for m in stored_messages(abc)] == ["failed"]

    def test_a_student_with_no_email_fails_and_is_recorded(
        self, abc: ConsultancyContext, set_up: None
    ) -> None:
        hari = make_student(abc, name="Hari Thapa", phone="9841000002")

        response = send(abc, hari, {"channel": "email", "body": "Namaste"})

        assert response.json()["status"] == "failed"
        assert response.json()["error"] == "This Student has no email address."
        assert FakeEmailGateway.sent == []

    def test_a_refusal_by_the_company_fails_with_its_reason(
        self, abc: ConsultancyContext, set_up: None, sita: Student
    ) -> None:
        FakeSmsGateway.failure = SMS_REFUSED

        response = send(abc, sita, {"channel": "sms", "body": "Namaste"})

        assert response.json()["status"] == "failed"
        assert response.json()["error"] == SMS_REFUSED
        assert stored_messages(abc)[0].error == SMS_REFUSED

    def test_an_email_the_server_refuses_fails_with_its_reason(
        self, abc: ConsultancyContext, set_up: None, sita: Student
    ) -> None:
        FakeEmailGateway.failure = EMAIL_REFUSED

        response = send(abc, sita, {"channel": "email", "body": "Namaste"})

        assert response.json()["status"] == "failed"
        assert response.json()["error"] == EMAIL_REFUSED
        assert response.json()["sent_to"] == "sita@example.com"

    def test_records_read_as_words(
        self, abc: ConsultancyContext, set_up: None, sita: Student
    ) -> None:
        send(abc, sita, {"channel": "sms", "body": "Namaste"})

        with tenant_context(abc.consultancy):
            assert str(Message.objects.select_related("student").get()) == (
                "SMS to Sita Gurung: Sent"
            )
            assert str(ProviderSettings.objects.get()) == "SMS and email settings"

    @pytest.mark.parametrize(
        ("channel", "refusal"), [("sms", SMS_NOT_SET_UP), ("email", EMAIL_NOT_SET_UP)]
    )
    def test_a_channel_not_set_up_is_refused_and_nothing_recorded(
        self,
        abc: ConsultancyContext,
        admin: Staff,
        sita: Student,
        channel: str,
        refusal: dict[str, list[str]],
    ) -> None:
        log_in(abc, admin)

        response = send(abc, sita, {"channel": channel, "body": "Namaste"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == refusal
        assert stored_messages(abc) == []

    def test_injection_shaped_text_is_sent_and_kept_exactly_as_typed(
        self, abc: ConsultancyContext, set_up: None, sita: Student
    ) -> None:
        for text in INJECTION_STRINGS:
            response = send(abc, sita, {"channel": "email", "body": text})
            assert response.json()["body"] == text

        assert [m.body for m in stored_messages(abc)] == INJECTION_STRINGS

    def test_logs_ids_only(
        self, abc: ConsultancyContext, set_up: None, sita: Student, json_logs: JsonLogs
    ) -> None:
        send(abc, sita, {"channel": "sms", "body": "Namaste secret words"})

        [message] = stored_messages(abc)
        [line] = json_logs.with_message("Message sent")
        assert line["message"] == (
            f"Message sent: message_id={message.pk}, student_id={sita.pk}, channel=sms, status=sent"
        )
        for private in ("Namaste secret words", "9841000001", SMS_TOKEN):
            assert private not in json_logs.text()


class TestRefusedMessages:
    @pytest.fixture(autouse=True)
    def ready(self, set_up: None) -> None:
        """SMS and email are set up and the Admin is logged in."""

    def _refused(self, abc: ConsultancyContext, sita: Student, body: object) -> dict[str, Any]:
        response = send(abc, sita, body)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_messages(abc) == []
        assert FakeSmsGateway.sent == []
        assert FakeEmailGateway.sent == []
        answer: dict[str, Any] = response.json()
        return answer

    def test_unknown_fields_are_refused(self, abc: ConsultancyContext, sita: Student) -> None:
        answer = self._refused(abc, sita, {"channel": "sms", "body": "Hi", "to": "9800000000"})

        assert answer == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"body": "Hi"}, "channel"),
            ({"channel": "sms"}, "body"),
            ({"channel": "whatsapp", "body": "Hi"}, "channel"),
            ({"channel": 1, "body": "Hi"}, "channel"),
            ({"channel": "sms", "body": ""}, "body"),
            ({"channel": "sms", "body": "   "}, "body"),
            ({"channel": "sms", "body": 42}, "body"),
            ({"channel": "sms", "body": None}, "body"),
            ({"channel": "sms", "body": ["Hi"]}, "body"),
            ({"channel": "sms", "body": "x" * 801}, "body"),
            ({"channel": "email", "body": "x" * 5001}, "body"),
            ({"channel": "sms", "body": "bell\x07"}, "body"),
        ],
    )
    def test_wrong_input_is_refused(
        self, abc: ConsultancyContext, sita: Student, body: dict[str, object], field: str
    ) -> None:
        assert field in self._refused(abc, sita, body)

    def test_the_longest_texts_are_accepted(self, abc: ConsultancyContext, sita: Student) -> None:
        responses = [
            send(abc, sita, {"channel": "sms", "body": "x" * 800}),
            send(abc, sita, {"channel": "email", "body": "x" * 5000}),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_201_CREATED] * 2

    @pytest.mark.parametrize("body", [["sms"], "sms", 7, None])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, sita: Student, body: object
    ) -> None:
        self._refused(abc, sita, body)

    def test_a_form_body_is_refused(self, abc: ConsultancyContext, sita: Student) -> None:
        response = abc.client.post(student_messages_url(sita), {"channel": "sms", "body": "Hi"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_an_unknown_student_is_not_found(self, abc: ConsultancyContext) -> None:
        response = send(abc, 999_999, {"channel": "sms", "body": "Hi"})

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestSeeingMessages:
    @pytest.fixture
    def sent(self, abc: ConsultancyContext, set_up: None, sita: Student) -> Student:
        """Two messages to Sita and one to Hari, oldest first; returns Hari."""
        hari = make_student(abc, name="Hari Thapa", phone="9841000002")
        send(abc, sita, {"channel": "sms", "body": "First"})
        send(abc, hari, {"channel": "sms", "body": "To Hari"})
        send(abc, sita, {"channel": "email", "body": "Second"})
        return hari

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_staff_see_a_students_messages_newest_first(
        self, abc: ConsultancyContext, sita: Student, sent: Student, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = abc.client.get(student_messages_url(sita))

        assert response.status_code == status.HTTP_200_OK
        assert [m["body"] for m in response.json()["results"]] == ["Second", "First"]
        assert response.json()["count"] == 2

    def test_the_messages_page_lists_everyones_newest_first(
        self, abc: ConsultancyContext, sent: Student
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(MESSAGES_URL)

        assert response.status_code == status.HTTP_200_OK
        answer = response.json()
        assert [m["body"] for m in answer["results"]] == ["Second", "To Hari", "First"]
        assert (answer["count"], answer["page"], answer["page_size"]) == (3, 1, 25)
        assert answer["results"][1]["student"] == {"id": sent.pk, "name": "Hari Thapa"}

    def test_the_list_pages(self, abc: ConsultancyContext, set_up: None, sita: Student) -> None:
        for number in range(26):
            send(abc, sita, {"channel": "sms", "body": f"Message {number}"})

        second = abc.client.get(MESSAGES_URL, {"page": 2}).json()

        assert [m["body"] for m in second["results"]] == ["Message 0"]
        assert abc.client.get(MESSAGES_URL, {"page": 3}).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("page", ["0", "two", "-1", "1.5"])
    def test_a_bad_page_is_refused(self, abc: ConsultancyContext, sent: Student, page: str) -> None:
        response = abc.client.get(MESSAGES_URL, {"page": page})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_teachers_see_no_messages_and_send_none(
        self, abc: ConsultancyContext, sita: Student, sent: Student
    ) -> None:
        abc.log_in_as(Role.TEACHER)

        responses = [
            abc.client.get(MESSAGES_URL),
            abc.client.get(student_messages_url(sita)),
            send(abc, sita, {"channel": "sms", "body": "Hi"}),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 3
        assert all(r.json() == NOT_FOR_TEACHERS for r in responses)
        assert len(stored_messages(abc)) == 3

    def test_logged_out_is_refused(self, abc: ConsultancyContext, sita: Student) -> None:
        logged_out = abc.new_client()

        responses = [
            logged_out.get(MESSAGES_URL),
            logged_out.get(student_messages_url(sita)),
            logged_out.post(student_messages_url(sita), {"channel": "sms"}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3

    def test_switched_off_they_are_hidden_and_kept(
        self, abc: ConsultancyContext, sita: Student, sent: Student
    ) -> None:
        switch_modules(abc.consultancy, set())

        responses = [
            abc.client.get(MESSAGES_URL),
            abc.client.get(student_messages_url(sita)),
            send(abc, sita, {"channel": "sms", "body": "Hi"}),
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
