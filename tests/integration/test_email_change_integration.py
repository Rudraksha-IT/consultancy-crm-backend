"""A Staff member changes their own email (spec 2026-11-22 stories 34-52; ticket 04).

My settings -> Email -> Change: the new email and current password (and a number from the
authenticator app, or a recovery code, when two-step login is on) send a Code to the new email;
the Code changes it. The old email gets a notice, every other device is signed out, this one
gets new tokens, and the Central login finds the person by the new email only.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from django.core import mail
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from freezegun import freeze_time
from pytest_django.fixtures import Settings
from rest_framework import status
from rest_framework.test import APIClient

from central_login.directory import email_hash
from central_login.models import StaffDirectoryEntry
from codes.models import OneTimeCode
from codes.services import MAX_ATTEMPTS, MAX_CODES_PER_HOUR
from codes.types import CodePurpose
from staff.models import Role, Staff
from tests.conftest import (
    PUBLIC_SCHEMA,
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    use_access_token,
)
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

REQUEST_URL = "/api/me/email/"
CONFIRM_URL = "/api/me/email/confirm/"
LOGIN_URL = "/api/auth/login/"
ME_URL = "/api/me/"
EMPLOYEES_URL = "/api/staff/"
SETUP_URL = "/api/auth/two-factor/setup/"
TWO_FACTOR_CONFIRM_URL = "/api/auth/two-factor/confirm/"
OLD_EMAIL = "sita@office.com"
NOT_THE_PASSWORD = "Not-the-password-1"  # noqa: S105  # test-only credential
NEW_EMAIL = "sita.sharma@office.com"
CODE_SENT = {"detail": "We've sent a 6-digit code to your new email."}
CODE_REFUSED = {
    "detail": "That code isn't right or has run out. Check the email, or send yourself a new one."
}
WRONG_PASSWORD = {"password": ["That isn't your current password."]}
SAME_EMAIL = {"email": ["That's already your email."]}
TAKEN = {"email": ["Someone here already uses this email."]}
NUMBER_NEEDED = {"code": ["Enter the 6-digit number from your authenticator app."]}
WRONG_NUMBER = {"code": ["That number isn't right. Enter the newest number from your app."]}
LIMIT_REACHED = {"detail": "Too many codes were sent. Wait an hour, then ask for a new one."}
SEND_FAILED = {"detail": "The email couldn't be sent. Try again in a moment."}
ONLY_STAFF = {"detail": "A Platform Admin has no account here to change."}
UNKNOWN_FIELD = {"detail": ["Unknown field."]}
INJECTION_STRINGS = [
    "<script>alert(1)</script>@office.com",
    "x'; DROP TABLE staff_staff; --@office.com",
    "}body{background:url(x)}@office.com",
    "sita@office.com<b>",
]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def sita(abc: ConsultancyContext) -> Staff:
    """A Counsellor of ABC, logged in on `abc.client`."""
    staff = abc.add_staff(Role.COUNSELLOR, email=OLD_EMAIL)
    use_access_token(abc.client, abc.tokens_for(staff).access)
    return staff


def post(client: APIClient, url: str, body: object) -> _MonkeyPatchedResponse:
    """POST `body` as JSON."""
    return client.post(url, body, format="json")


def ask(
    client: APIClient, email: object = NEW_EMAIL, password: object = TEST_PASSWORD, **more: object
) -> _MonkeyPatchedResponse:
    """Ask for a Code to move to `email`."""
    return post(client, REQUEST_URL, {"email": email, "password": password, **more})


def confirm(client: APIClient, code: str, email: object = NEW_EMAIL) -> _MonkeyPatchedResponse:
    """Finish the change with the emailed Code."""
    return post(client, CONFIRM_URL, {"email": email, "code": code})


def emailed_code(index: int = -1) -> str:
    """The 6-digit Code in an email sent during the test (the last one by default)."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[index].body))
    assert found is not None
    return found.group(1)


def html_of(message: EmailMessage) -> str:
    """The HTML part of a sent email."""
    assert isinstance(message, EmailMultiAlternatives)
    [(html, _)] = message.alternatives
    return str(html)


def wrong(code: str) -> str:
    """A different 6-digit Code."""
    return f"{(int(code) + 1) % 1_000_000:06d}"


def stored(office: ConsultancyContext, staff: Staff) -> Staff:
    """The Staff member as stored now."""
    with tenant_context(office.consultancy):
        return Staff.objects.get(pk=staff.pk)


def log_in(office: ConsultancyContext, email: str) -> _MonkeyPatchedResponse:
    """Log in on a new device."""
    return post(office.new_client(), LOGIN_URL, {"email": email, "password": TEST_PASSWORD})


def directory() -> set[tuple[str, str]]:
    """Every Central login entry as (email hash, schema)."""
    with schema_context(PUBLIC_SCHEMA):
        return set(
            StaffDirectoryEntry.objects.values_list("email_hash", "consultancy__schema_name")
        )


def change_email(office: ConsultancyContext, client: APIClient) -> _MonkeyPatchedResponse:
    """The whole change: ask, then confirm with the Code emailed."""
    assert ask(client).status_code == status.HTTP_202_ACCEPTED
    return confirm(client, emailed_code())


def set_up_two_factor(office: ConsultancyContext, staff: Staff) -> tuple[APIClient, str, list[str]]:
    """Switch two-step login on for `staff` and set it up.

    Returns a logged-in client, the app's secret and the recovery codes.
    """
    with tenant_context(office.consultancy):
        Staff.objects.filter(pk=staff.pk).update(two_factor_required=True)
    client = office.new_client()
    login = post(client, LOGIN_URL, {"email": staff.email, "password": TEST_PASSWORD}).json()
    uri = post(client, SETUP_URL, {"challenge": login["challenge"]}).json()["uri"]
    secret = parse_qs(urlparse(uri).query)["secret"][0]
    answer = post(
        client,
        TWO_FACTOR_CONFIRM_URL,
        {"challenge": login["challenge"], "code": pyotp.TOTP(secret).now()},
    ).json()
    use_access_token(client, answer["access"])
    return client, secret, answer["recovery_codes"]


class TestTheWholeChange:
    def test_asking_sends_a_code_to_the_new_email_only(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        response = ask(abc.client)

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json() == CODE_SENT
        [message] = mail.outbox
        assert message.to == [NEW_EMAIL]
        assert "ABC Education" in message.subject
        assert "30 minutes" in message.body
        assert re.fullmatch(r"\d{6}", emailed_code())
        assert stored(abc, sita).email == OLD_EMAIL

    def test_the_code_changes_the_email_and_keeps_this_device_logged_in(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        response = change_email(abc, abc.client)

        assert response.status_code == status.HTTP_200_OK
        answer = response.json()
        assert set(answer) == {"access", "refresh", "email"}
        assert answer["email"] == NEW_EMAIL
        assert stored(abc, sita).email == NEW_EMAIL
        use_access_token(abc.client, answer["access"])
        assert abc.client.get(ME_URL).json()["email"] == NEW_EMAIL

    def test_every_other_device_is_signed_out(self, abc: ConsultancyContext, sita: Staff) -> None:
        other = abc.new_client()
        use_access_token(other, abc.tokens_for(sita).access)
        assert other.get(ME_URL).status_code == status.HTTP_200_OK

        change_email(abc, abc.client)

        assert other.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_new_email_logs_in_and_the_old_one_does_not(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        change_email(abc, abc.client)

        assert log_in(abc, NEW_EMAIL).status_code == status.HTTP_200_OK
        assert log_in(abc, OLD_EMAIL).status_code == status.HTTP_400_BAD_REQUEST

    def test_the_old_email_gets_a_notice(self, abc: ConsultancyContext, sita: Staff) -> None:
        change_email(abc, abc.client)

        notice = mail.outbox[-1]
        assert notice.to == [OLD_EMAIL]
        assert "ABC Education" in notice.subject
        assert f"Your email was changed to {NEW_EMAIL}." in notice.body
        assert "If this wasn't you, tell your Admin." in notice.body
        assert not re.search(r"\b\d{6}\b", str(notice.body))

    def test_the_central_login_finds_the_new_email_only(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        change_email(abc, abc.client)

        entries = directory()
        assert (email_hash(NEW_EMAIL), "abcedu") in entries
        assert (email_hash(OLD_EMAIL), "abcedu") not in entries

    def test_employees_show_the_new_email(self, abc: ConsultancyContext, sita: Staff) -> None:
        admin_client = abc.new_client()
        use_access_token(admin_client, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        change_email(abc, abc.client)

        emails = {row["email"] for row in admin_client.get(EMPLOYEES_URL).json()}
        assert NEW_EMAIL in emails
        assert OLD_EMAIL not in emails

    def test_the_email_is_stored_trimmed_and_lower_case(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        ask(abc.client, email=" Sita.Sharma@Office.COM ")

        response = confirm(abc.client, emailed_code(), email="SITA.SHARMA@office.com")

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, sita).email == NEW_EMAIL

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.FRONT_DESK, Role.TEACHER])
    def test_every_role_can_change_their_own(self, abc: ConsultancyContext, role: Role) -> None:
        staff = abc.log_in_as(role)

        response = change_email(abc, abc.client)

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, staff).email == NEW_EMAIL

    def test_a_new_code_replaces_the_old_one(self, abc: ConsultancyContext, sita: Staff) -> None:
        ask(abc.client)
        first = emailed_code()
        ask(abc.client)
        second = emailed_code()

        if first != second:
            assert confirm(abc.client, first).json() == CODE_REFUSED
        assert confirm(abc.client, second).status_code == status.HTTP_200_OK

    def test_the_code_and_password_never_reach_the_logs(
        self, abc: ConsultancyContext, sita: Staff, json_logs: JsonLogs
    ) -> None:
        change_email(abc, abc.client)
        code = emailed_code(0)

        text = json_logs.text()
        assert code not in text
        assert TEST_PASSWORD not in text
        assert NEW_EMAIL not in text
        assert OLD_EMAIL not in text
        assert json_logs.with_message(f"Email changed: staff_id={sita.pk}")


class TestTwoStepLogin:
    def test_a_number_is_needed_when_two_step_login_is_on(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email=OLD_EMAIL)
        client, secret, _ = set_up_two_factor(abc, staff)

        missing = ask(client)
        with freeze_time(timezone.now() + timedelta(seconds=31)):
            right = ask(client, code=pyotp.TOTP(secret).at(datetime.now(tz=UTC)))

        assert (missing.status_code, missing.json()) == (status.HTTP_400_BAD_REQUEST, NUMBER_NEEDED)
        assert right.status_code == status.HTTP_202_ACCEPTED, right.json()
        assert [message.to for message in mail.outbox] == [[NEW_EMAIL]]

    def test_a_wrong_number_is_refused(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email=OLD_EMAIL)
        client, secret, _ = set_up_two_factor(abc, staff)
        now = datetime.now(tz=UTC)
        near = {pyotp.TOTP(secret).at(now + timedelta(seconds=30 * n)) for n in (-1, 0, 1)}
        bad = next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in near)

        response = ask(client, code=bad)

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            WRONG_NUMBER,
        )
        assert mail.outbox == []

    def test_a_used_number_is_refused(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email=OLD_EMAIL)
        client, secret, _ = set_up_two_factor(abc, staff)

        # The number that finished setup was used then; it never works twice.
        response = ask(client, code=pyotp.TOTP(secret).now())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []

    def test_a_recovery_code_works_once(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email=OLD_EMAIL)
        client, _, codes = set_up_two_factor(abc, staff)

        first = ask(client, recovery_code=codes[0])
        again = ask(client, recovery_code=codes[0])

        assert first.status_code == status.HTTP_202_ACCEPTED
        assert again.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_number_and_a_recovery_code_together_are_refused(
        self, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email=OLD_EMAIL)
        client, secret, codes = set_up_two_factor(abc, staff)

        response = ask(client, code=pyotp.TOTP(secret).now(), recovery_code=codes[0])

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []

    def test_a_wrong_password_never_uses_up_a_recovery_code(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR, email=OLD_EMAIL)
        client, _, codes = set_up_two_factor(abc, staff)

        refused = ask(client, password=NOT_THE_PASSWORD, recovery_code=codes[0])
        accepted = ask(client, recovery_code=codes[0])

        assert refused.json() == WRONG_PASSWORD
        assert accepted.status_code == status.HTTP_202_ACCEPTED


class TestRefusedAsking:
    def test_a_wrong_password_is_refused(self, abc: ConsultancyContext, sita: Staff) -> None:
        response = ask(abc.client, password=NOT_THE_PASSWORD)

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            WRONG_PASSWORD,
        )
        assert mail.outbox == []

    def test_the_same_email_is_refused(self, abc: ConsultancyContext, sita: Staff) -> None:
        response = ask(abc.client, email=" SITA@office.com")

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, SAME_EMAIL)
        assert mail.outbox == []

    def test_an_email_someone_here_uses_is_refused(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        abc.add_staff(Role.ADMIN, email=NEW_EMAIL)

        response = ask(abc.client, email=NEW_EMAIL.upper())

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, TAKEN)
        assert mail.outbox == []

    def test_an_email_used_in_another_office_is_fine(
        self, abc: ConsultancyContext, sita: Staff, make_consultancy: ConsultancyFactory
    ) -> None:
        make_consultancy("XYZ Overseas", "xyzoverseas").add_staff(Role.ADMIN, email=NEW_EMAIL)

        assert ask(abc.client).status_code == status.HTTP_202_ACCEPTED

    def test_the_hourly_limit_stops_more_codes(self, abc: ConsultancyContext, sita: Staff) -> None:
        for _ in range(MAX_CODES_PER_HOUR):
            assert ask(abc.client).status_code == status.HTTP_202_ACCEPTED

        response = ask(abc.client)

        assert (response.status_code, response.json()) == (
            status.HTTP_429_TOO_MANY_REQUESTS,
            LIMIT_REACHED,
        )
        assert len(mail.outbox) == MAX_CODES_PER_HOUR

    def test_a_send_failure_changes_nothing(
        self, abc: ConsultancyContext, sita: Staff, settings: Settings
    ) -> None:
        settings.EMAIL_BACKEND = "tests.fakes.BrokenEmailBackend"

        response = ask(abc.client)

        assert (response.status_code, response.json()) == (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            SEND_FAILED,
        )
        assert "550" not in str(response.content)
        assert stored(abc, sita).email == OLD_EMAIL
        with tenant_context(abc.consultancy):
            assert not OneTimeCode.objects.filter(
                staff=sita, purpose=CodePurpose.EMAIL_CHANGE, replaced_at__isnull=True
            ).exists()

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": NEW_EMAIL},
            {"password": TEST_PASSWORD},
            {"email": [NEW_EMAIL], "password": TEST_PASSWORD},
            {"email": {"a": NEW_EMAIL}, "password": TEST_PASSWORD},
            {"email": 12345, "password": TEST_PASSWORD},
            {"email": NEW_EMAIL, "password": 12345},
            {"email": "not-an-email", "password": TEST_PASSWORD},
            {"email": "", "password": TEST_PASSWORD},
            {"email": f"{'a' * 250}@office.com", "password": TEST_PASSWORD},
            {"email": NEW_EMAIL, "password": "x" * 1000},
            {"email": NEW_EMAIL, "password": TEST_PASSWORD, "code": "12345"},
            {"email": NEW_EMAIL, "password": TEST_PASSWORD, "code": "<b>12</b>"},
            {"email": NEW_EMAIL, "password": TEST_PASSWORD, "recovery_code": "' OR 1=1"},
            [NEW_EMAIL],
            "email",
        ],
    )
    def test_bad_bodies_are_refused(
        self, abc: ConsultancyContext, sita: Staff, body: object
    ) -> None:
        response = post(abc.client, REQUEST_URL, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []

    @pytest.mark.parametrize("email", INJECTION_STRINGS)
    def test_injection_shaped_emails_are_refused(
        self, abc: ConsultancyContext, sita: Staff, email: str
    ) -> None:
        response = ask(abc.client, email=email)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []
        assert stored(abc, sita).email == OLD_EMAIL

    def test_unknown_fields_are_refused(self, abc: ConsultancyContext, sita: Staff) -> None:
        response = ask(abc.client, staff_id=str(sita.public_id))

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            UNKNOWN_FIELD,
        )
        assert mail.outbox == []


class TestRefusedConfirming:
    def test_a_wrong_code_is_refused_and_changes_nothing(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        ask(abc.client)

        response = confirm(abc.client, wrong(emailed_code()))

        assert (response.status_code, response.json()) == (
            status.HTTP_400_BAD_REQUEST,
            CODE_REFUSED,
        )
        assert stored(abc, sita).email == OLD_EMAIL
        assert len(mail.outbox) == 1

    def test_the_code_works_only_once(self, abc: ConsultancyContext, sita: Staff) -> None:
        ask(abc.client)
        code = emailed_code()
        renewed = confirm(abc.client, code).json()
        use_access_token(abc.client, renewed["access"])

        again = confirm(abc.client, code)

        assert again.json() == CODE_REFUSED
        assert stored(abc, sita).email == NEW_EMAIL

    def test_an_expired_code_is_refused(self, abc: ConsultancyContext, sita: Staff) -> None:
        ask(abc.client)
        code = emailed_code()
        with tenant_context(abc.consultancy):
            OneTimeCode.objects.filter(staff=sita).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )

        assert confirm(abc.client, code).json() == CODE_REFUSED
        assert stored(abc, sita).email == OLD_EMAIL

    def test_a_code_for_another_email_is_refused(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        ask(abc.client)

        response = confirm(abc.client, emailed_code(), email="someone.else@office.com")

        assert response.json() == CODE_REFUSED
        assert stored(abc, sita).email == OLD_EMAIL

    def test_a_code_for_another_person_is_refused(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        ask(abc.client)
        code = emailed_code()
        other = abc.new_client()
        hari = abc.add_staff(Role.FRONT_DESK, email="hari@office.com")
        use_access_token(other, abc.tokens_for(hari).access)

        response = confirm(other, code)

        assert response.json() == CODE_REFUSED
        assert stored(abc, hari).email == "hari@office.com"
        assert stored(abc, sita).email == OLD_EMAIL

    def test_a_password_reset_code_is_refused(self, abc: ConsultancyContext, sita: Staff) -> None:
        post(abc.new_client(), "/api/auth/password-reset/", {"email": OLD_EMAIL})

        assert confirm(abc.client, emailed_code()).json() == CODE_REFUSED

    def test_too_many_wrong_codes_end_the_code(self, abc: ConsultancyContext, sita: Staff) -> None:
        ask(abc.client)
        code = emailed_code()
        for _ in range(MAX_ATTEMPTS):
            confirm(abc.client, wrong(code))

        assert confirm(abc.client, code).json() == CODE_REFUSED
        assert stored(abc, sita).email == OLD_EMAIL

    def test_an_email_taken_meanwhile_is_refused(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        ask(abc.client)
        abc.add_staff(Role.ADMIN, email=NEW_EMAIL)

        response = confirm(abc.client, emailed_code())

        assert (response.status_code, response.json()) == (status.HTTP_400_BAD_REQUEST, TAKEN)
        assert stored(abc, sita).email == OLD_EMAIL

    def test_a_notice_that_cannot_be_sent_still_keeps_the_change(
        self, abc: ConsultancyContext, sita: Staff, settings: Settings
    ) -> None:
        ask(abc.client)
        code = emailed_code()
        settings.EMAIL_BACKEND = "tests.fakes.BrokenEmailBackend"

        response = confirm(abc.client, code)

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, sita).email == NEW_EMAIL

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"code": "123456"},
            {"email": NEW_EMAIL},
            {"email": NEW_EMAIL, "code": "12345"},
            {"email": NEW_EMAIL, "code": "1234567"},
            {"email": NEW_EMAIL, "code": 123456},
            {"email": NEW_EMAIL, "code": "12345a"},
            {"email": NEW_EMAIL, "code": "<b>1</b>"},
            {"email": "not-an-email", "code": "123456"},
            {"email": [NEW_EMAIL], "code": "123456"},
            {"email": NEW_EMAIL, "code": "123456", "staff_id": 1},
        ],
    )
    def test_bad_bodies_are_refused(
        self, abc: ConsultancyContext, sita: Staff, body: object
    ) -> None:
        ask(abc.client)

        response = post(abc.client, CONFIRM_URL, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, sita).email == OLD_EMAIL


class TestEmails:
    def test_both_emails_carry_the_office_look_and_reply_to(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        abc.consultancy.contact_email = "hello@office.com"
        abc.consultancy.save(update_fields=["contact_email"])

        change_email(abc, abc.client)

        code_email, notice = mail.outbox
        for message in (code_email, notice):
            assert message.reply_to == ["hello@office.com"]
            assert "ABC Education" in message.from_email
            assert "ABC Education" in html_of(message)

    def test_names_with_markup_come_out_escaped(self, abc: ConsultancyContext, sita: Staff) -> None:
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=sita.pk).update(name="<script>alert(1)</script>")

        change_email(abc, abc.client)

        for message in mail.outbox:
            html = html_of(message)
            assert "<script>alert(1)</script>" not in html
            assert "&lt;script&gt;" in html


class TestWhoCanChange:
    def test_logged_out_callers_are_refused(self, abc: ConsultancyContext, sita: Staff) -> None:
        client = abc.new_client()

        assert ask(client).status_code == status.HTTP_401_UNAUTHORIZED
        assert confirm(client, "123456").status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_platform_admin_in_support_access_is_refused(
        self, abc: ConsultancyContext, sita: Staff, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, abc)

        asked = ask(visit.client)
        confirmed = confirm(visit.client, "123456")

        assert (asked.status_code, asked.json()) == (status.HTTP_403_FORBIDDEN, ONLY_STAFF)
        assert (confirmed.status_code, confirmed.json()) == (status.HTTP_403_FORBIDDEN, ONLY_STAFF)
        assert mail.outbox == []

    def test_an_admin_cannot_change_someone_elses_email(
        self, abc: ConsultancyContext, sita: Staff
    ) -> None:
        admin_client = abc.new_client()
        use_access_token(admin_client, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        response = admin_client.patch(
            f"{EMPLOYEES_URL}{sita.public_id}/", {"email": NEW_EMAIL}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, sita).email == OLD_EMAIL

    @pytest.mark.parametrize("url", [REQUEST_URL, CONFIRM_URL])
    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(
        self, abc: ConsultancyContext, sita: Staff, url: str, method: str
    ) -> None:
        response = getattr(abc.client, method)(url)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_the_platform_host_has_no_email_change(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        assert ask(platform_site.client).status_code == status.HTTP_404_NOT_FOUND
