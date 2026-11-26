"""An Admin invites back someone who has left (UI revamp, ticket 18).

They become invited again with a new Code, keeping their id, Role and every record they are on.
Cancelling that invite returns them to Has left; it never deletes them.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from django.core import mail
from django_tenants.utils import schema_context, tenant_context
from rest_framework import status

from central_login.directory import email_hash
from central_login.models import StaffDirectoryEntry
from codes.models import OneTimeCode
from codes.services import MAX_CODES_PER_HOUR
from staff.models import Role, Staff, StaffStatus
from tests.conftest import (
    PUBLIC_SCHEMA,
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    use_access_token,
)
from tests.integration.test_staff_two_factor_integration import events_of, is_set_up, set_up
from two_factor.models import EventKind

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

STAFF_URL = "/api/staff/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
CONTEXT_URL = "/api/context/"
JOIN_CODE_URL = "/api/auth/join/code/"
JOIN_COMPLETE_URL = "/api/auth/join/complete/"
NEW_PASSWORD = "New-Horse-7-Staple!"  # noqa: S105  # test-only credential
ONLY_LEFT = {"detail": "Only someone who has left can be invited back."}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE staff_staff; --",
    "}body{background:url(javascript:alert(1))}",
]


def invite_back_url(staff_id: object) -> str:
    """Invite back one employee who has left."""
    return f"{STAFF_URL}{staff_id}/invite-back/"


def invite_url(staff_id: object) -> str:
    """Send again or cancel one employee's invite."""
    return f"{STAFF_URL}{staff_id}/invite/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """An office with an Admin logged in on its client."""
    office = make_consultancy("ABC Education", "abcedu")
    office.log_in_as(Role.ADMIN)
    return office


@pytest.fixture
def leaver(abc: ConsultancyContext) -> Staff:
    """A Counsellor who has left."""
    return leave(abc, abc.add_staff(Role.COUNSELLOR))


def leave(office: ConsultancyContext, staff: Staff) -> Staff:
    """Mark `staff` as having left, as the office's Admin."""
    response = office.client.post(f"{STAFF_URL}{staff.public_id}/deactivate/")
    assert response.status_code == status.HTTP_200_OK, response.content
    return staff


def stored(office: ConsultancyContext, staff: Staff) -> Staff:
    """The Staff member as saved now."""
    with tenant_context(office.consultancy):
        return Staff.objects.get(pk=staff.pk)


def emailed_code() -> str:
    """The 6-digit Code in the last email."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert found is not None
    return found.group(1)


def in_directory(office: ConsultancyContext, email: str) -> bool:
    """Whether the Central login knows `email` at this office."""
    with schema_context(PUBLIC_SCHEMA):
        return StaffDirectoryEntry.objects.filter(
            email_hash=email_hash(email), consultancy=office.consultancy
        ).exists()


def join(office: ConsultancyContext, staff: Staff, code: str) -> _MonkeyPatchedResponse:
    """Finish joining with the Code and a new password."""
    return office.new_client().post(
        JOIN_COMPLETE_URL,
        {"email": staff.email, "code": code, "password": NEW_PASSWORD},
        format="json",
    )


def log_in(office: ConsultancyContext, staff: Staff, password: str) -> _MonkeyPatchedResponse:
    """Try the password on the office's login."""
    return office.new_client().post(
        LOGIN_URL, {"email": staff.email, "password": password}, format="json"
    )


class TestInviteBack:
    def test_someone_who_left_is_invited_again_and_emailed_a_code(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        response = abc.client.post(invite_back_url(leaver.public_id))

        assert response.status_code == status.HTTP_202_ACCEPTED
        body = response.json()
        assert body["id"] == str(leaver.public_id)
        assert body["status"] == "invited"
        assert body["invited_back"] is True
        assert body["role"] == "counsellor"
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [leaver.email]
        check = abc.new_client().post(
            JOIN_CODE_URL, {"email": leaver.email, "code": emailed_code()}, format="json"
        )
        assert check.status_code == status.HTTP_200_OK

    def test_their_record_is_kept_as_it_was(self, abc: ConsultancyContext, leaver: Staff) -> None:
        abc.client.post(invite_back_url(leaver.public_id))

        kept = stored(abc, leaver)
        assert kept.pk == leaver.pk
        assert kept.public_id == leaver.public_id
        assert (kept.name, kept.email, kept.role) == (leaver.name, leaver.email, leaver.role)
        assert (kept.job_title, kept.joined_on) == (leaver.job_title, leaver.joined_on)
        assert kept.status == StaffStatus.INVITED
        assert kept.invited_back is True

    def test_their_old_password_no_longer_works(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))

        assert log_in(abc, leaver, TEST_PASSWORD).status_code == status.HTTP_400_BAD_REQUEST
        assert not stored(abc, leaver).has_usable_password()

    def test_they_are_back_in_the_central_login_directory(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        assert not in_directory(abc, leaver.email)

        abc.client.post(invite_back_url(leaver.public_id))

        assert in_directory(abc, leaver.email)

    def test_their_two_step_login_is_reset(self, abc: ConsultancyContext, admin_id: int) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        set_up(abc, counsellor)
        leave(abc, counsellor)

        abc.client.post(invite_back_url(counsellor.public_id))

        assert not is_set_up(abc, counsellor)
        last = events_of(abc, counsellor)[-1]
        assert last.what == EventKind.RESET
        assert last.by_staff_id == admin_id

    def test_no_old_login_of_theirs_works(self, abc: ConsultancyContext) -> None:
        counsellor = abc.add_staff(Role.COUNSELLOR)
        tokens = abc.tokens_for(counsellor)
        leave(abc, counsellor)

        abc.client.post(invite_back_url(counsellor.public_id))

        theirs = abc.new_client()
        use_access_token(theirs, tokens.access)
        assert theirs.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED
        refresh = abc.new_client().post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")
        assert refresh.status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_list_shows_them_as_invited_back(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))

        rows = {row["id"]: row for row in abc.client.get(STAFF_URL).json()}

        assert rows[str(leaver.public_id)]["status"] == "invited"
        assert rows[str(leaver.public_id)]["invited_back"] is True
        assert [row["invited_back"] for row in rows.values()].count(True) == 1

    def test_a_new_invite_is_not_marked_invited_back(self, abc: ConsultancyContext) -> None:
        response = abc.client.post(
            STAFF_URL,
            {
                "name": "Sita Sharma",
                "mobile": "9812345670",
                "email": "sita@abcedu.test",
                "job_title": "Counsellor",
                "role": "counsellor",
                "joined_on": "2026-11-01",
            },
            format="json",
        )

        assert response.json()["invited_back"] is False

    def test_send_the_code_again_works_for_them(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))

        response = abc.client.post(invite_url(leaver.public_id))

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json()["invited_back"] is True
        assert len(mail.outbox) == 2

    def test_the_code_limit_applies_and_nothing_changes(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))
        for _ in range(MAX_CODES_PER_HOUR - 1):
            abc.client.post(invite_url(leaver.public_id))
        # Back to Has left without the cancel (which deletes the Codes), to meet the limit.
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=leaver.pk).update(
                status=StaffStatus.DEACTIVATED, invited_back=False
            )

        response = abc.client.post(invite_back_url(leaver.public_id))

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert response.json() == {
            "detail": "Too many codes were sent in the last hour. Try again later."
        }
        assert len(mail.outbox) == MAX_CODES_PER_HOUR
        assert stored(abc, leaver).status == StaffStatus.DEACTIVATED
        assert stored(abc, leaver).invited_back is False

    def test_it_is_logged_with_ids_only(
        self, abc: ConsultancyContext, leaver: Staff, admin_id: int, json_logs: JsonLogs
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))

        json_logs.only(f"Invited back: staff_id={leaver.pk}, by_staff_id={admin_id}")
        assert leaver.email not in json_logs.text()
        assert leaver.name not in json_logs.text()


class TestOnlyForSomeoneWhoLeft:
    def test_someone_working_here_is_refused(self, abc: ConsultancyContext) -> None:
        active = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.post(invite_back_url(active.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONLY_LEFT
        assert stored(abc, active).status == StaffStatus.ACTIVE
        assert mail.outbox == []
        assert log_in(abc, active, TEST_PASSWORD).status_code == status.HTTP_200_OK

    def test_someone_already_invited_is_refused(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))

        response = abc.client.post(invite_back_url(leaver.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONLY_LEFT
        assert len(mail.outbox) == 1

    def test_an_admin_cannot_invite_themselves_back(
        self, abc: ConsultancyContext, admin_id: int
    ) -> None:
        with tenant_context(abc.consultancy):
            me = Staff.objects.get(pk=admin_id)

        response = abc.client.post(invite_back_url(me.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_200_OK

    def test_unknown_ids_are_not_found(self, abc: ConsultancyContext, leaver: Staff) -> None:
        unknown = "00000000-0000-4000-8000-000000000001"

        assert abc.client.post(invite_back_url(unknown)).status_code == status.HTTP_404_NOT_FOUND
        assert abc.client.post(invite_back_url(leaver.pk)).status_code == status.HTTP_404_NOT_FOUND
        assert stored(abc, leaver).status == StaffStatus.DEACTIVATED


class TestPermissions:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused(
        self, abc: ConsultancyContext, leaver: Staff, role: Role
    ) -> None:
        other = abc.new_client()
        use_access_token(
            other, abc.tokens_for(abc.add_staff(role, email="other@abcedu.test")).access
        )

        response = other.post(invite_back_url(leaver.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == {"detail": "Only an Admin can manage Employees."}
        assert stored(abc, leaver).status == StaffStatus.DEACTIVATED
        assert mail.outbox == []

    def test_logged_out_callers_are_refused(self, abc: ConsultancyContext, leaver: Staff) -> None:
        response = abc.new_client().post(invite_back_url(leaver.public_id))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert stored(abc, leaver).status == StaffStatus.DEACTIVATED


class TestCancel:
    def test_cancelling_returns_them_to_has_left_and_keeps_their_record(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))
        code = emailed_code()

        response = abc.client.delete(invite_url(leaver.public_id))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["id"] == str(leaver.public_id)
        assert response.json()["status"] == "deactivated"
        assert response.json()["invited_back"] is False
        kept = stored(abc, leaver)
        assert kept.status == StaffStatus.DEACTIVATED
        assert kept.invited_back is False
        assert (kept.name, kept.role) == (leaver.name, leaver.role)
        assert not in_directory(abc, leaver.email)
        with tenant_context(abc.consultancy):
            assert not OneTimeCode.objects.filter(staff=kept).exists()
        check = abc.new_client().post(
            JOIN_CODE_URL, {"email": leaver.email, "code": code}, format="json"
        )
        assert check.status_code == status.HTTP_400_BAD_REQUEST

    def test_they_can_be_invited_back_again_after_a_cancel(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))
        abc.client.delete(invite_url(leaver.public_id))

        response = abc.client.post(invite_back_url(leaver.public_id))

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert stored(abc, leaver).status == StaffStatus.INVITED

    def test_a_new_invites_cancel_still_removes_the_person(self, abc: ConsultancyContext) -> None:
        invited = abc.client.post(
            STAFF_URL,
            {
                "name": "Sita Sharma",
                "mobile": "9812345670",
                "email": "sita@abcedu.test",
                "job_title": "Counsellor",
                "role": "counsellor",
                "joined_on": "2026-11-01",
            },
            format="json",
        ).json()

        response = abc.client.delete(invite_url(invited["id"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        with tenant_context(abc.consultancy):
            assert not Staff.objects.filter(public_id=invited["id"]).exists()

    def test_the_cancel_is_logged_with_ids_only(
        self, abc: ConsultancyContext, leaver: Staff, admin_id: int, json_logs: JsonLogs
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))

        abc.client.delete(invite_url(leaver.public_id))

        json_logs.only(
            f"Invite cancelled, back to has left: staff_id={leaver.pk}, by_staff_id={admin_id}"
        )
        assert leaver.email not in json_logs.text()


class TestJoiningAgain:
    def test_joining_makes_them_active_with_their_new_password(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))

        response = join(abc, leaver, emailed_code())

        assert response.status_code == status.HTTP_200_OK
        back = stored(abc, leaver)
        assert back.status == StaffStatus.ACTIVE
        assert back.invited_back is False
        assert back.pk == leaver.pk
        assert log_in(abc, leaver, NEW_PASSWORD).status_code == status.HTTP_200_OK
        assert log_in(abc, leaver, TEST_PASSWORD).status_code == status.HTTP_400_BAD_REQUEST

    def test_once_joined_the_list_no_longer_marks_them(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))
        join(abc, leaver, emailed_code())

        rows = {row["id"]: row for row in abc.client.get(STAFF_URL).json()}

        assert rows[str(leaver.public_id)]["status"] == "active"
        assert rows[str(leaver.public_id)]["invited_back"] is False

    def test_once_joined_a_cancel_no_longer_touches_them(
        self, abc: ConsultancyContext, leaver: Staff
    ) -> None:
        abc.client.post(invite_back_url(leaver.public_id))
        join(abc, leaver, emailed_code())

        response = abc.client.delete(invite_url(leaver.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, leaver).status == StaffStatus.ACTIVE


class TestRefusedInput:
    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(
        self, abc: ConsultancyContext, leaver: Staff, method: str
    ) -> None:
        response = getattr(abc.client, method)(invite_back_url(leaver.public_id))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert stored(abc, leaver).status == StaffStatus.DEACTIVATED

    @pytest.mark.parametrize(
        "body",
        [
            {"role": "admin"},
            {"status": "active"},
            {"email": "someone.else@abcedu.test"},
            {"password": "Kathmandu!2026"},
            {"invited_back": False},
            *({"name": value} for value in INJECTION_STRINGS),
        ],
    )
    def test_unknown_fields_are_refused_and_never_stored(
        self, abc: ConsultancyContext, leaver: Staff, body: dict[str, object]
    ) -> None:
        response = abc.client.post(invite_back_url(leaver.public_id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        kept = stored(abc, leaver)
        assert kept.status == StaffStatus.DEACTIVATED
        assert (kept.name, kept.email, kept.role) == (leaver.name, leaver.email, leaver.role)
        assert mail.outbox == []

    @pytest.mark.parametrize("body", [[], ["x"], "text", 7])
    def test_a_body_that_is_not_an_object_is_refused(
        self, abc: ConsultancyContext, leaver: Staff, body: object
    ) -> None:
        response = abc.client.post(invite_back_url(leaver.public_id), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, leaver).status == StaffStatus.DEACTIVATED

    def test_an_empty_object_is_taken(self, abc: ConsultancyContext, leaver: Staff) -> None:
        response = abc.client.post(invite_back_url(leaver.public_id), {}, format="json")

        assert response.status_code == status.HTTP_202_ACCEPTED

    @pytest.mark.parametrize("value", INJECTION_STRINGS)
    def test_injection_shaped_ids_are_not_found(
        self, abc: ConsultancyContext, leaver: Staff, value: str
    ) -> None:
        response = abc.client.post(f"{STAFF_URL}{value}/invite-back/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored(abc, leaver).status == StaffStatus.DEACTIVATED


@pytest.fixture
def admin_id(abc: ConsultancyContext) -> int:
    """The number of the Admin logged in on the office's client."""
    with tenant_context(abc.consultancy):
        return Staff.objects.get(role=Role.ADMIN).pk
