"""An Admin manages the office's Employees: invite, change Role, deactivate (spec 18-21; issue 07).

The Admin never sees or sets a password: adding someone emails them a Code to join with.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from django.core import mail
from django_tenants.utils import tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from codes.services import MAX_CODES_PER_HOUR
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from tests.conftest import (
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    use_access_token,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

STAFF_URL = "/api/staff/"
COUNSELLORS_URL = "/api/staff/counsellors/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
CONTEXT_URL = "/api/context/"
JOIN_CODE_URL = "/api/auth/join/code/"
ONLY_ADMIN = {"detail": "Only an Admin can manage Employees."}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE staff_staff; --",
    "}body{background:url(javascript:alert(1))}",
]


def staff_url(staff_id: int) -> str:
    """One employee."""
    return f"{STAFF_URL}{staff_id}/"


def deactivate_url(staff_id: int) -> str:
    """Deactivate one employee."""
    return f"{STAFF_URL}{staff_id}/deactivate/"


def invite_url(staff_id: int) -> str:
    """Send again or cancel one employee's invite."""
    return f"{STAFF_URL}{staff_id}/invite/"


def invite_body(**changes: object) -> dict[str, object]:
    """What the Add an employee form sends."""
    return {
        "name": "Sita Sharma",
        "mobile": "9812345670",
        "email": "sita@abcedu.test",
        "job_title": "Counsellor",
        "role": "counsellor",
        "joined_on": "2026-11-01",
        **changes,
    }


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def admin(abc: ConsultancyContext) -> Staff:
    """An Admin of ABC, logged in on `abc.client`."""
    return abc.log_in_as(Role.ADMIN)


def invite(client: APIClient, **changes: object) -> _MonkeyPatchedResponse:
    """Add an employee with `invite_body`, changed by `changes`."""
    return client.post(STAFF_URL, invite_body(**changes), format="json")


def emailed_code(index: int = -1) -> str:
    """The 6-digit Code in an email sent during the test (the last one by default)."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[index].body))
    assert found is not None
    return found.group(1)


def stored(consultancy: ConsultancyContext, staff_id: int) -> Staff | None:
    """The Staff member as stored, or None if removed."""
    with tenant_context(consultancy.consultancy):
        return Staff.objects.filter(pk=staff_id).first()


def logged_in_client(consultancy: ConsultancyContext, staff: Staff) -> APIClient:
    """A new client logged in as `staff`."""
    client = consultancy.new_client()
    use_access_token(client, consultancy.tokens_for(staff).access)
    return client


@pytest.mark.usefixtures("admin")
class TestList:
    def test_lists_every_employee_with_their_details(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        invite(abc.client)

        response = abc.client.get(STAFF_URL)

        assert response.status_code == status.HTTP_200_OK
        by_email = {row["email"]: row for row in response.json()}
        assert by_email["sita@abcedu.test"] == {
            "id": by_email["sita@abcedu.test"]["id"],
            "name": "Sita Sharma",
            "email": "sita@abcedu.test",
            "mobile": "9812345670",
            "job_title": "Counsellor",
            "role": "counsellor",
            "joined_on": "2026-11-01",
            "status": "invited",
            "two_factor_required": False,
            "two_factor_set_up": False,
        }
        assert by_email[admin.email]["status"] == "active"

    def test_shows_who_has_left(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        abc.client.post(deactivate_url(counsellor.pk))

        rows = abc.client.get(STAFF_URL).json()

        assert [r["status"] for r in rows if r["id"] == counsellor.pk] == ["deactivated"]

    def test_platform_admins_never_appear(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        platform_site.add_admin(email="ops@platform.test")

        emails = [row["email"] for row in abc.client.get(STAFF_URL).json()]

        assert "ops@platform.test" not in emails
        assert len(emails) == 1

    def test_passwords_never_appear(self, abc: ConsultancyContext) -> None:
        invite(abc.client)

        text = abc.client.get(STAFF_URL).content.decode()

        assert "password" not in text


class TestPermissions:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused_everything(self, abc: ConsultancyContext, role: Role) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abcedu.test")
        abc.log_in_as(role)

        answers = [
            abc.client.get(STAFF_URL),
            invite(abc.client),
            abc.client.patch(staff_url(other.pk), {"role": "admin"}, format="json"),
            abc.client.post(deactivate_url(other.pk)),
            abc.client.post(invite_url(other.pk)),
            abc.client.delete(invite_url(other.pk)),
        ]

        assert [a.status_code for a in answers] == [status.HTTP_403_FORBIDDEN] * len(answers)
        assert answers[0].json() == ONLY_ADMIN
        assert mail.outbox == []
        assert stored(abc, other.pk) is not None
        assert stored(abc, other.pk).role == Role.COUNSELLOR  # type: ignore[union-attr]

    def test_logged_out_callers_are_refused(self, abc: ConsultancyContext) -> None:
        client = abc.new_client()

        assert client.get(STAFF_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert invite(client).status_code == status.HTTP_401_UNAUTHORIZED
        assert client.get(COUNSELLORS_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_platform_host_has_no_staff_list(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        assert platform_site.client.get(STAFF_URL).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.usefixtures("admin")
class TestInvite:
    def test_adding_an_employee_invites_them_and_emails_a_code(
        self, abc: ConsultancyContext
    ) -> None:
        response = invite(abc.client)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["status"] == "invited"
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["sita@abcedu.test"]
        assert message.subject == "Your code to join ABC Education"
        assert "as Counsellor" in message.body
        assert f"http://{abc.host}/join" in message.body
        assert "30 minutes" in message.body
        assert re.fullmatch(r"\d{6}", emailed_code())

    def test_the_link_in_the_email_uses_the_forwarded_address(
        self, abc: ConsultancyContext
    ) -> None:
        abc.client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"

        invite(abc.client)

        assert f"https://{abc.host}/join" in mail.outbox[0].body

    def test_the_invited_person_has_no_password_and_cannot_log_in(
        self, abc: ConsultancyContext
    ) -> None:
        staff_id = invite(abc.client).json()["id"]

        staff = stored(abc, staff_id)
        login = abc.new_client().post(
            LOGIN_URL, {"email": "sita@abcedu.test", "password": TEST_PASSWORD}, format="json"
        )

        assert staff is not None
        assert not staff.has_usable_password()
        assert not staff.is_active
        assert login.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_email_is_stored_trimmed_and_lowercase(self, abc: ConsultancyContext) -> None:
        response = invite(abc.client, email=" Sita@ABCedu.Test ")

        assert response.json()["email"] == "sita@abcedu.test"

    def test_an_email_already_used_here_is_refused(self, abc: ConsultancyContext) -> None:
        invite(abc.client)
        mail.outbox.clear()

        response = invite(abc.client, email="SITA@abcedu.test")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"email": ["Someone here already uses this email."]}
        assert mail.outbox == []

    def test_the_same_email_added_twice_at_once_is_refused(
        self, abc: ConsultancyContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        invite(abc.client)
        mail.outbox.clear()
        # The second request passed the form check before the first one was saved.
        monkeypatch.setattr("staff.serializers.is_email_taken", lambda _email: False)

        response = invite(abc.client)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"email": ["Someone here already uses this email."]}
        assert mail.outbox == []

    def test_spaces_and_dashes_in_the_mobile_are_removed(self, abc: ConsultancyContext) -> None:
        response = invite(abc.client, mobile="+977 981-234-5670")

        assert response.json()["mobile"] == "+9779812345670"

    def test_an_admin_can_be_invited(self, abc: ConsultancyContext) -> None:
        assert invite(abc.client, role="admin").json()["role"] == "admin"

    def test_teacher_is_refused_while_classes_are_off(self, abc: ConsultancyContext) -> None:
        response = invite(abc.client, role="teacher")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"role": ["Teachers need the test-prep classes feature."]}

    def test_teacher_is_allowed_while_classes_are_on(self, abc: ConsultancyContext) -> None:
        switch_modules(abc.consultancy, [Module.BATCHES])

        assert invite(abc.client, role="teacher").json()["role"] == "teacher"

    def test_the_invite_is_logged_with_ids_only(
        self, abc: ConsultancyContext, admin: Staff, json_logs: JsonLogs
    ) -> None:
        staff_id = invite(abc.client).json()["id"]

        entry = json_logs.only(f"Employee invited: staff_id={staff_id}, by_staff_id={admin.pk}")
        assert entry["level"] == "INFO"
        assert "sita" not in json_logs.text().lower()
        assert emailed_code() not in json_logs.text()


@pytest.mark.usefixtures("admin")
class TestResendAndCancelInvite:
    def test_send_the_code_again_replaces_the_old_code(self, abc: ConsultancyContext) -> None:
        staff_id = invite(abc.client).json()["id"]
        first = emailed_code()

        response = abc.client.post(invite_url(staff_id))

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json()["status"] == "invited"
        assert len(mail.outbox) == 2
        second = emailed_code()
        old = abc.new_client().post(
            JOIN_CODE_URL, {"email": "sita@abcedu.test", "code": first}, format="json"
        )
        new = abc.new_client().post(
            JOIN_CODE_URL, {"email": "sita@abcedu.test", "code": second}, format="json"
        )
        if first != second:
            assert old.status_code == status.HTTP_400_BAD_REQUEST
        assert new.status_code == status.HTTP_200_OK

    def test_too_many_codes_in_an_hour_are_refused(self, abc: ConsultancyContext) -> None:
        staff_id = invite(abc.client).json()["id"]
        for _ in range(MAX_CODES_PER_HOUR - 1):
            abc.client.post(invite_url(staff_id))

        response = abc.client.post(invite_url(staff_id))

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert response.json() == {
            "detail": "Too many codes were sent in the last hour. Try again later."
        }
        assert len(mail.outbox) == MAX_CODES_PER_HOUR

    def test_cancelling_removes_the_person_and_ends_the_code(self, abc: ConsultancyContext) -> None:
        staff_id = invite(abc.client).json()["id"]
        code = emailed_code()

        response = abc.client.delete(invite_url(staff_id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert stored(abc, staff_id) is None
        check = abc.new_client().post(
            JOIN_CODE_URL, {"email": "sita@abcedu.test", "code": code}, format="json"
        )
        assert check.status_code == status.HTTP_400_BAD_REQUEST

    def test_after_cancelling_the_email_can_be_invited_again(self, abc: ConsultancyContext) -> None:
        staff_id = invite(abc.client).json()["id"]
        abc.client.delete(invite_url(staff_id))

        assert invite(abc.client).status_code == status.HTTP_201_CREATED

    def test_people_who_joined_have_no_invite_to_resend_or_cancel(
        self, abc: ConsultancyContext
    ) -> None:
        joined = abc.add_staff(Role.COUNSELLOR)

        resend = abc.client.post(invite_url(joined.pk))
        cancel = abc.client.delete(invite_url(joined.pk))

        assert resend.status_code == status.HTTP_400_BAD_REQUEST
        assert cancel.status_code == status.HTTP_400_BAD_REQUEST
        assert resend.json() == {"detail": "Only someone who hasn't joined yet has an invite."}
        assert stored(abc, joined.pk) is not None
        assert mail.outbox == []

    def test_unknown_ids_are_not_found(self, abc: ConsultancyContext) -> None:
        assert abc.client.post(invite_url(9999)).status_code == status.HTTP_404_NOT_FOUND
        assert abc.client.delete(invite_url(9999)).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.usefixtures("admin")
class TestChangeRole:
    def test_the_role_changes_and_takes_effect_at_once(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        their_client = logged_in_client(abc, counsellor)

        response = abc.client.patch(staff_url(counsellor.pk), {"role": "admin"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["role"] == "admin"
        assert their_client.get(CONTEXT_URL).json()["staff"]["role"] == "admin"
        assert their_client.get(STAFF_URL).status_code == status.HTTP_200_OK

    def test_an_invited_persons_role_can_change(self, abc: ConsultancyContext) -> None:
        staff_id = invite(abc.client).json()["id"]

        response = abc.client.patch(staff_url(staff_id), {"role": "front_desk"}, format="json")

        assert response.json()["role"] == "front_desk"

    def test_an_admin_cannot_change_their_own_role(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        response = abc.client.patch(staff_url(admin.pk), {"role": "counsellor"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "You can't change your own Role or account."}
        assert stored(abc, admin.pk).role == Role.ADMIN  # type: ignore[union-attr]

    def test_someone_who_has_left_cannot_be_changed(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        abc.client.post(deactivate_url(counsellor.pk))

        response = abc.client.patch(staff_url(counsellor.pk), {"role": "admin"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "This person has left the office."}

    def test_teacher_is_refused_while_classes_are_off(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.patch(staff_url(counsellor.pk), {"role": "teacher"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"role": ["Teachers need the test-prep classes feature."]}

    def test_a_teacher_keeps_their_role_after_classes_are_switched_off(
        self, abc: ConsultancyContext
    ) -> None:
        teacher = abc.add_staff(Role.TEACHER)

        response = abc.client.patch(staff_url(teacher.pk), {"role": "teacher"}, format="json")

        assert response.status_code == status.HTTP_200_OK

    def test_the_change_is_logged(
        self, abc: ConsultancyContext, admin: Staff, json_logs: JsonLogs
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)

        abc.client.patch(staff_url(counsellor.pk), {"role": "admin"}, format="json")

        json_logs.only(
            f"Employee role changed: staff_id={counsellor.pk}, role=admin, by_staff_id={admin.pk}"
        )

    def test_unknown_ids_are_not_found(self, abc: ConsultancyContext) -> None:
        response = abc.client.patch(staff_url(9999), {"role": "admin"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.usefixtures("admin")
class TestDeactivate:
    def test_deactivated_staff_cannot_log_in(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.post(deactivate_url(counsellor.pk))
        login = abc.new_client().post(
            LOGIN_URL, {"email": counsellor.email, "password": TEST_PASSWORD}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "deactivated"
        assert login.status_code == status.HTTP_400_BAD_REQUEST

    def test_their_open_logins_end_at_once(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        tokens = abc.tokens_for(counsellor)
        their_client = logged_in_client(abc, counsellor)

        abc.client.post(deactivate_url(counsellor.pk))

        assert their_client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        refresh = abc.new_client().post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")
        assert refresh.status_code == status.HTTP_401_UNAUTHORIZED

    def test_their_name_and_record_are_kept(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)

        abc.client.post(deactivate_url(counsellor.pk))

        kept = stored(abc, counsellor.pk)
        assert kept is not None
        assert kept.name == counsellor.name
        assert kept.status == StaffStatus.DEACTIVATED

    def test_an_admin_cannot_deactivate_themselves(
        self, abc: ConsultancyContext, admin: Staff
    ) -> None:
        response = abc.client.post(deactivate_url(admin.pk))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK

    def test_an_invite_is_cancelled_rather_than_deactivated(self, abc: ConsultancyContext) -> None:
        staff_id = invite(abc.client).json()["id"]

        response = abc.client.post(deactivate_url(staff_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Cancel the invite instead."}

    def test_someone_who_has_left_cannot_be_deactivated_again(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        abc.client.post(deactivate_url(counsellor.pk))

        response = abc.client.post(deactivate_url(counsellor.pk))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_they_get_no_password_reset_code(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        abc.client.post(deactivate_url(counsellor.pk))

        abc.new_client().post("/api/auth/password-reset/", {"email": counsellor.email})

        assert mail.outbox == []

    def test_the_deactivation_is_logged(
        self, abc: ConsultancyContext, admin: Staff, json_logs: JsonLogs
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)

        abc.client.post(deactivate_url(counsellor.pk))

        json_logs.only(f"Employee deactivated: staff_id={counsellor.pk}, by_staff_id={admin.pk}")


class TestCounsellorChoices:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_admins_and_counsellors_who_are_active_can_be_picked(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        admin = abc.add_staff(Role.ADMIN, email="admin@abcedu.test")
        counsellor = abc.add_staff(Role.COUNSELLOR, email="ram@abcedu.test")
        left = abc.add_staff(Role.COUNSELLOR, email="left@abcedu.test")
        abc.add_staff(Role.FRONT_DESK, email="desk@abcedu.test")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=left.pk).update(status=StaffStatus.DEACTIVATED)
        use_access_token(abc.client, abc.tokens_for(abc.add_staff(role)).access)

        response = abc.client.get(COUNSELLORS_URL)

        assert response.status_code == status.HTTP_200_OK
        ids = {row["id"] for row in response.json()}
        assert {admin.pk, counsellor.pk} <= ids
        assert left.pk not in ids
        assert all(set(row) == {"id", "name", "role"} for row in response.json())
        assert all(row["role"] in {"admin", "counsellor"} for row in response.json())

    def test_teachers_are_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.TEACHER)

        assert abc.client.get(COUNSELLORS_URL).status_code == status.HTTP_403_FORBIDDEN

    def test_only_get_is_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(COUNSELLORS_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


@pytest.mark.usefixtures("admin")
class TestRefusedInput:
    @pytest.mark.parametrize(
        ("url", "method"),
        [
            (STAFF_URL, "put"),
            (STAFF_URL, "delete"),
            ("staff", "get"),
            ("staff", "put"),
            ("staff", "delete"),
            ("deactivate", "get"),
            ("invite", "get"),
        ],
    )
    def test_only_the_needed_methods_are_allowed(
        self, abc: ConsultancyContext, url: str, method: str
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        path = {
            "staff": staff_url(counsellor.pk),
            "deactivate": deactivate_url(counsellor.pk),
            "invite": invite_url(counsellor.pk),
        }.get(url, url)

        response = getattr(abc.client, method)(path)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("field", ["name", "mobile", "email", "job_title", "role", "joined_on"])
    def test_every_field_is_required(self, abc: ConsultancyContext, field: str) -> None:
        body = invite_body()
        del body[field]

        response = abc.client.post(STAFF_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    @pytest.mark.parametrize("field", ["name", "job_title", "mobile"])
    def test_blank_text_is_refused(self, abc: ConsultancyContext, field: str) -> None:
        response = invite(abc.client, **{field: "   "})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", 12),
            ("name", ["Sita"]),
            ("mobile", 9812345670),
            ("email", True),
            ("job_title", {"x": 1}),
            ("role", 1),
            ("joined_on", 20261101),
            ("joined_on", "01/11/2026"),
            ("joined_on", "2026-02-30"),
            ("role", "owner"),
            ("role", "Admin"),
        ],
    )
    def test_wrong_types_and_values_are_refused(
        self, abc: ConsultancyContext, field: str, value: object
    ) -> None:
        response = invite(abc.client, **{field: value})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        assert mail.outbox == []

    @pytest.mark.parametrize(
        ("field", "length"), [("name", 201), ("job_title", 101), ("mobile", 21), ("email", 255)]
    )
    def test_over_long_values_are_refused(
        self, abc: ConsultancyContext, field: str, length: int
    ) -> None:
        value = "a" * (length - len("@abcedu.test")) + "@abcedu.test" if field == "email" else None
        response = invite(abc.client, **{field: value or "9" * length})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()

    @pytest.mark.parametrize("mobile", ["98123", "98123456789012345", "98-ab-1234", "+"])
    def test_mobiles_that_are_not_phone_numbers_are_refused(
        self, abc: ConsultancyContext, mobile: str
    ) -> None:
        response = invite(abc.client, mobile=mobile)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "mobile": ["Enter a mobile number of 7 to 15 digits, optionally starting with +."]
        }

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        response = invite(abc.client, password="Kathmandu!2026", status="active")  # noqa: S106

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        with tenant_context(abc.consultancy):
            assert not Staff.objects.filter(email="sita@abcedu.test").exists()

    def test_the_role_change_takes_only_the_role(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.patch(
            staff_url(counsellor.pk), {"role": "admin", "email": "x@y.test"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored(abc, counsellor.pk).role == Role.COUNSELLOR  # type: ignore[union-attr]

    @pytest.mark.parametrize("body", [[], "admin", None, {"role": None}, {}])
    def test_bad_role_change_bodies_are_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.patch(staff_url(counsellor.pk), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("value", INJECTION_STRINGS)
    def test_injection_shaped_text_is_stored_only_as_text(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        response = invite(abc.client, name=value, job_title=value)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["name"] == value
        assert response.json()["job_title"] == value
        assert response["Content-Type"] == "application/json"
        with tenant_context(abc.consultancy):
            assert Staff.objects.count() == 2  # the Admin and the new person

    @pytest.mark.parametrize("value", [*INJECTION_STRINGS, "a\nb@x.test"])
    def test_injection_shaped_emails_and_mobiles_are_refused(
        self, abc: ConsultancyContext, value: str
    ) -> None:
        assert invite(abc.client, email=value).status_code == status.HTTP_400_BAD_REQUEST
        assert invite(abc.client, mobile=value).status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []

    @pytest.mark.parametrize("name", ["Sita\nBcc: x@y.test", "Sita\x00"])
    def test_control_characters_in_names_are_refused(
        self, abc: ConsultancyContext, name: str
    ) -> None:
        response = invite(abc.client, name=name)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in response.json()

    @pytest.mark.parametrize("staff_id", ["abc", "-1", "1.5"])
    def test_ids_that_are_not_whole_numbers_are_not_found(
        self, abc: ConsultancyContext, staff_id: str
    ) -> None:
        response = abc.client.patch(f"{STAFF_URL}{staff_id}/", {"role": "admin"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
