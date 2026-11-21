"""Transfer Requests: ask to take a Student, approve, decline, cancel, close (spec 44a-44e; 57)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.db import IntegrityError, transaction
from django_tenants.utils import tenant_context
from rest_framework import status

from history.models import HistoryEntry, HistoryKind
from notifications.models import Notification
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_consultancy_status_integration import set_status
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
    stored,
    transfer_url,
)
from tests.support_access import open_as_platform_admin
from transfer_requests.models import TransferRequest, TransferRequestStatus

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

LIST_URL = "/api/transfer-requests/"
COUNT_URL = "/api/transfer-requests/count/"
REASON = "Sarina's sister Rojina is my Student, and the family wants one counsellor for both."
TEACHERS = {"detail": "Teachers can't see Students."}
ASK_ROLES = {"detail": "Only counsellors and Admins can ask to take a Student."}
ALREADY_YOURS = {"detail": "This Student is already yours."}
ALREADY_ASKED = {"detail": "Someone has already asked to take this Student."}
NOT_WAITING = {"detail": "This request is no longer waiting for an answer."}
ANSWER_ASSIGNED = {"detail": "Only the Student's counsellor or an Admin can answer this request."}
ANSWER_UNASSIGNED = {
    "detail": "Only Front Desk or an Admin can answer for a Student with no counsellor."
}
OWN_REQUEST = {"detail": "You can't answer your own request."}
ONLY_REQUESTER = {"detail": "Only the person who asked can cancel this request."}
ONLY_ADMIN_ALL = {"detail": "Only an Admin can see everyone's transfer requests."}
REASON_NEEDED = {"reason": ["Say why you should take this Student."]}
TRANSFERRED_ANOTHER_WAY = "The Student was given to a counsellor another way."
REQUESTER_LEFT = "The person who asked has left the office."


def ask_url(student: Student | int | str) -> str:
    """Where a Transfer Request for the Student is sent."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/transfer-requests/"


def action_url(request_id: object, action: str) -> str:
    """Approve, decline or cancel one request."""
    return f"/api/transfer-requests/{request_id}/{action}/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


class Office:
    """ABC's people: two counsellors, an Admin, Front Desk and a Teacher, and Sarina."""

    def __init__(self, office: ConsultancyContext) -> None:
        self.office = office
        self.bikash = named_staff(office, "Bikash Thapa")
        self.anjali = named_staff(office, "Anjali Gurung")
        self.admin = named_staff(office, "Nabin Adhikari", Role.ADMIN)
        self.desk = named_staff(office, "Pooja Shrestha", Role.FRONT_DESK)
        self.teacher = named_staff(office, "Ramesh Karki", Role.TEACHER)
        self.sarina = make_student(office, self.bikash, Stage.COUNSELLING, name="Sarina Khadka")

    def as_(self, staff: Staff) -> ConsultancyContext:
        """Log the office's client in as `staff`."""
        log_in(self.office, staff)
        return self.office

    def ask(
        self, by: Staff, student: Student | None = None, reason: object = REASON
    ) -> _MonkeyPatchedResponse:
        """`by` asks to take the Student (Sarina unless given)."""
        target = student or self.sarina
        return self.as_(by).client.post(ask_url(target), {"reason": reason}, format="json")

    def asked(self, by: Staff, student: Student | None = None) -> str:
        """`by` asks for the Student; return the request's id (its UUID)."""
        response = self.ask(by, student)
        assert response.status_code == status.HTTP_201_CREATED, response.content
        request_id: str = response.json()["id"]
        return request_id

    def act(
        self, by: Staff, request_id: str | int, action: str, body: dict[str, object] | None = None
    ) -> _MonkeyPatchedResponse:
        """`by` approves, declines or cancels the request."""
        url = action_url(request_id, action)
        return self.as_(by).client.post(url, body or {}, format="json")

    def stored_request(self, request_id: str) -> TransferRequest:
        """The request as stored now."""
        with tenant_context(self.office.consultancy):
            return TransferRequest.objects.get(public_id=request_id)

    def notifications_of(self, staff: Staff) -> list[str]:
        """The kinds of `staff`'s notifications, newest first."""
        with tenant_context(self.office.consultancy):
            return list(
                Notification.objects.filter(recipient=staff)
                .order_by("-pk")
                .values_list("kind", flat=True)
            )

    def history(self, student: Student | None = None) -> list[HistoryEntry]:
        """The Student's History, oldest first."""
        with tenant_context(self.office.consultancy):
            target = student or self.sarina
            return list(HistoryEntry.objects.filter(student=target).order_by("at", "pk"))


@pytest.fixture
def people(abc: ConsultancyContext) -> Office:
    """ABC's people and Sarina, Bikash's Student."""
    return Office(abc)


class TestAsking:
    def test_a_counsellor_asks_for_someone_elses_student(self, people: Office) -> None:
        response = people.ask(people.anjali, reason=f"  {REASON} ")

        assert response.status_code == status.HTTP_201_CREATED
        answer = response.json()
        assert answer["student"] == {"id": str(people.sarina.public_id), "name": "Sarina Khadka"}
        assert answer["asked_by"] == {"id": str(people.anjali.public_id), "name": "Anjali Gurung"}
        assert answer["reason"] == REASON
        assert answer["status"] == "waiting"
        assert answer["counsellor"] == {"id": str(people.bikash.public_id), "name": "Bikash Thapa"}
        assert answer["answered_by"] is None
        assert answer["answered_at"] is None
        assert answer["answer_reason"] == ""
        assert answer["taken_from"] == "Bikash Thapa"
        assert (answer["can_answer"], answer["can_cancel"]) == (False, True)
        stored_request = people.stored_request(answer["id"])
        assert stored_request.status == TransferRequestStatus.WAITING
        assert stored_request.asked_by_id == people.anjali.pk

    def test_an_admin_may_ask_too(self, people: Office) -> None:
        assert people.ask(people.admin).status_code == status.HTTP_201_CREATED

    def test_a_student_with_no_counsellor_may_be_asked_for(self, people: Office) -> None:
        lead = make_student(people.office, name="Dipesh Rana")

        response = people.ask(people.anjali, lead)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["counsellor"] is None

    def test_a_lost_student_may_be_asked_for(self, people: Office) -> None:
        lost = make_student(people.office, people.bikash, Stage.LOST, lost_reason="Moved away.")

        assert people.ask(people.anjali, lost).status_code == status.HTTP_201_CREATED

    def test_asking_for_your_own_student_is_refused(self, people: Office) -> None:
        response = people.ask(people.bikash)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ALREADY_YOURS

    def test_front_desk_is_refused(self, people: Office) -> None:
        response = people.ask(people.desk)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ASK_ROLES

    def test_a_teacher_is_refused(self, people: Office) -> None:
        response = people.ask(people.teacher)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == TEACHERS

    def test_only_one_request_waits_per_student(self, people: Office) -> None:
        people.asked(people.anjali)

        response = people.ask(people.admin)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == ALREADY_ASKED
        with tenant_context(people.office.consultancy):
            assert TransferRequest.objects.count() == 1

    def test_the_database_keeps_at_most_one_waiting_per_student(self, people: Office) -> None:
        people.asked(people.anjali)

        with (
            tenant_context(people.office.consultancy),
            pytest.raises(IntegrityError),
            transaction.atomic(),
        ):
            TransferRequest.objects.create(
                student=people.sarina, asked_by=people.admin, reason="Second"
            )

    def test_after_an_answer_someone_may_ask_again(self, people: Office) -> None:
        request_id = people.asked(people.anjali)
        people.act(people.bikash, request_id, "decline")

        assert people.ask(people.admin).status_code == status.HTTP_201_CREATED

    def test_whoever_can_answer_is_notified(self, people: Office) -> None:
        second_admin = named_staff(people.office, "Kamala Rai", Role.ADMIN)
        left = named_staff(people.office, "Hari Left", Role.ADMIN)
        with tenant_context(people.office.consultancy):
            Staff.objects.filter(pk=left.pk).update(status=StaffStatus.DEACTIVATED)

        people.asked(people.anjali)

        for answerer in (people.bikash, people.admin, second_admin):
            assert people.notifications_of(answerer) == ["transfer_asked"]
        for nobody in (people.anjali, people.desk, people.teacher, left):
            assert people.notifications_of(nobody) == []

    def test_front_desk_is_notified_for_a_student_with_no_counsellor(self, people: Office) -> None:
        lead = make_student(people.office, name="Dipesh Rana")

        people.asked(people.anjali, lead)

        assert people.notifications_of(people.desk) == ["transfer_asked"]
        assert people.notifications_of(people.admin) == ["transfer_asked"]
        assert people.notifications_of(people.bikash) == []

    def test_an_admin_who_asks_is_not_notified_of_their_own_request(self, people: Office) -> None:
        people.asked(people.admin)

        assert people.notifications_of(people.admin) == []
        assert people.notifications_of(people.bikash) == ["transfer_asked"]

    def test_the_notification_names_who_asked_and_the_student(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        [item] = people.as_(people.bikash).client.get("/api/notifications/").json()["results"]

        assert item["kind"] == "transfer_asked"
        assert item["task"] is None
        assert item["by"] == {"id": str(people.anjali.public_id), "name": "Anjali Gurung"}
        assert item["student"] == {"id": str(people.sarina.public_id), "name": "Sarina Khadka"}
        assert item["transfer_request"] == {"id": request_id, "status": "waiting"}

    def test_the_request_is_logged_without_the_reason(
        self, people: Office, json_logs: JsonLogs
    ) -> None:
        request_id = people.asked(people.anjali)
        with tenant_context(people.office.consultancy):  # logs keep the number
            number = TransferRequest.objects.get(public_id=request_id).pk

        [entry] = json_logs.with_message("Transfer request sent")
        assert f"transfer_request_id={number}" in entry["message"]
        assert REASON not in json_logs.text()

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"reason": ""},
            {"reason": "   "},
        ],
    )
    def test_a_reason_is_required(self, people: Office, body: dict[str, object]) -> None:
        response = people.as_(people.anjali).client.post(
            ask_url(people.sarina), body, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == REASON_NEEDED

    @pytest.mark.parametrize("reason", [None, 42, True, ["x"], {"a": 1}, "x" * 501, "a\x00b"])
    def test_bad_reasons_are_refused(self, people: Office, reason: object) -> None:
        response = people.ask(people.anjali, reason=reason)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "reason" in response.json()
        with tenant_context(people.office.consultancy):
            assert not TransferRequest.objects.exists()

    def test_a_reason_of_five_hundred_characters_with_line_breaks_is_kept(
        self, people: Office
    ) -> None:
        reason = "Same family.\nOne counsellor." + "x" * 471

        assert people.ask(people.anjali, reason=reason).json()["reason"] == reason

    @pytest.mark.parametrize("reason", INJECTION_STRINGS)
    def test_injection_shaped_reasons_are_kept_as_plain_text(
        self, people: Office, reason: str
    ) -> None:
        assert people.ask(people.anjali, reason=reason).json()["reason"] == reason

    def test_unknown_fields_are_refused_and_never_stored(self, people: Office) -> None:
        response = people.as_(people.anjali).client.post(
            ask_url(people.sarina),
            {"reason": REASON, "status": "approved", "asked_by": people.admin.pk},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        with tenant_context(people.office.consultancy):
            assert not TransferRequest.objects.exists()

    def test_a_body_that_is_not_an_object_is_refused(self, people: Office) -> None:
        response = people.as_(people.anjali).client.post(
            ask_url(people.sarina), [REASON], format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_form_body_is_refused(self, people: Office) -> None:
        response = people.as_(people.anjali).client.post(ask_url(people.sarina), {"reason": REASON})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, people: Office, method: str) -> None:
        client = people.as_(people.anjali).client

        response = getattr(client, method)(ask_url(people.sarina), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_student_who_does_not_exist_is_not_found(self, people: Office) -> None:
        client = people.as_(people.anjali).client

        response = client.post(ask_url(999_999), {"reason": REASON}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, people: Office) -> None:
        client = people.office.new_client()

        response = client.post(ask_url(people.sarina), {"reason": REASON}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_a_read_only_office_refuses_asking(self, people: Office, value: str) -> None:
        set_status(people.office, value)

        assert people.ask(people.anjali).status_code == status.HTTP_403_FORBIDDEN
        with tenant_context(people.office.consultancy):
            assert not TransferRequest.objects.exists()


class TestApproving:
    def test_the_counsellor_approves_and_the_student_is_transferred(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.bikash, request_id, "approve")

        assert response.status_code == status.HTTP_200_OK
        answer = response.json()
        assert answer["status"] == "approved"
        assert answer["answered_by"] == {"name": "Bikash Thapa", "is_platform_admin": False}
        assert answer["taken_from"] == "Bikash Thapa"
        assert answer["counsellor"] == {"id": str(people.anjali.public_id), "name": "Anjali Gurung"}
        assert answer["answered_at"] is not None
        assert stored(people.office, people.sarina).assigned_counsellor_id == people.anjali.pk

    def test_history_names_who_asked_and_who_approved(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        people.act(people.admin, request_id, "approve")

        [entry] = people.history()
        assert entry.kind == HistoryKind.TRANSFERRED
        assert (entry.before, entry.after) == ("Bikash Thapa", "Anjali Gurung")
        assert entry.by_name == "Nabin Adhikari"
        assert entry.detail == "Asked by Anjali Gurung, approved by Nabin Adhikari."

    def test_the_new_counsellor_can_change_the_student_and_the_old_one_cant(
        self, people: Office
    ) -> None:
        request_id = people.asked(people.anjali)
        people.act(people.bikash, request_id, "approve")

        profile = people.as_(people.anjali).client.get(f"/api/students/{people.sarina.public_id}/")
        old = people.as_(people.bikash).client.get(f"/api/students/{people.sarina.public_id}/")

        assert profile.json()["can_change"] is True
        assert old.json()["can_change"] is False

    def test_front_desk_approves_for_a_student_with_no_counsellor(self, people: Office) -> None:
        lead = make_student(people.office, name="Dipesh Rana")
        request_id = people.asked(people.anjali, lead)

        response = people.act(people.desk, request_id, "approve")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["taken_from"] == ""
        assert stored(people.office, lead).assigned_counsellor_id == people.anjali.pk
        [entry] = people.history(lead)
        assert entry.kind == HistoryKind.COUNSELLOR_ASSIGNED
        assert entry.after == "Anjali Gurung"
        assert entry.detail == "Asked by Anjali Gurung, approved by Pooja Shrestha."

    def test_the_asker_is_notified(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        people.act(people.bikash, request_id, "approve")

        assert people.notifications_of(people.anjali) == ["transfer_approved"]
        [item] = people.as_(people.anjali).client.get("/api/notifications/").json()["results"]
        assert item["by"] == {"id": str(people.bikash.public_id), "name": "Bikash Thapa"}
        assert item["transfer_request"] == {"id": request_id, "status": "approved"}

    @pytest.mark.parametrize("who", ["anjali", "desk", "teacher"])
    def test_anyone_else_is_refused(self, people: Office, who: str) -> None:
        other = named_staff(people.office, "Sunita Other")
        request_id = people.asked(other)
        expected = {"anjali": ANSWER_ASSIGNED, "desk": ANSWER_ASSIGNED, "teacher": TEACHERS}

        response = people.act(getattr(people, who), request_id, "approve")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == expected[who]
        assert stored(people.office, people.sarina).assigned_counsellor_id == people.bikash.pk
        assert people.stored_request(request_id).status == TransferRequestStatus.WAITING

    def test_a_counsellor_cant_answer_for_a_student_with_no_counsellor(
        self, people: Office
    ) -> None:
        lead = make_student(people.office, name="Dipesh Rana")
        request_id = people.asked(people.anjali, lead)

        response = people.act(people.bikash, request_id, "approve")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ANSWER_UNASSIGNED

    def test_an_admin_cant_approve_their_own_request(self, people: Office) -> None:
        request_id = people.asked(people.admin)

        response = people.act(people.admin, request_id, "approve")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == OWN_REQUEST

    def test_an_answered_request_cant_be_answered_again(self, people: Office) -> None:
        request_id = people.asked(people.anjali)
        people.act(people.bikash, request_id, "decline")

        response = people.act(people.admin, request_id, "approve")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == NOT_WAITING
        assert stored(people.office, people.sarina).assigned_counsellor_id == people.bikash.pk

    def test_a_platform_admin_approves_under_their_own_name(
        self, people: Office, platform_site: PlatformContext
    ) -> None:
        request_id = people.asked(people.anjali)
        visit = open_as_platform_admin(platform_site, people.office)

        response = visit.client.post(action_url(request_id, "approve"), {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["answered_by"] == {
            "name": "Platform Ops (Platform Admin)",
            "is_platform_admin": True,
        }
        [entry] = people.history()
        assert entry.by_platform_admin is True
        assert entry.by_staff_id is None
        assert entry.detail == "Asked by Anjali Gurung, approved by Platform Ops (Platform Admin)."
        stored_request = people.stored_request(request_id)
        assert stored_request.answered_by_id is None

    @pytest.mark.parametrize("body", [{"reason": "x"}, {"status": "approved"}, ["x"]])
    def test_approve_takes_an_empty_body(self, people: Office, body: object) -> None:
        request_id = people.asked(people.anjali)

        response = people.as_(people.bikash).client.post(
            action_url(request_id, "approve"), body, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert people.stored_request(request_id).status == TransferRequestStatus.WAITING

    def test_an_unknown_request_is_not_found(self, people: Office) -> None:
        response = people.act(people.admin, 999_999, "approve")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("action", ["approve", "decline", "cancel"])
    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, people: Office, action: str, method: str) -> None:
        request_id = people.asked(people.anjali)
        client = people.as_(people.admin).client

        response = getattr(client, method)(action_url(request_id, action), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_a_read_only_office_refuses_answers(self, people: Office, value: str) -> None:
        request_id = people.asked(people.anjali)
        set_status(people.office, value)

        response = people.act(people.bikash, request_id, "approve")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert people.stored_request(request_id).status == TransferRequestStatus.WAITING

    def test_an_asker_who_can_no_longer_counsel_cant_be_given_the_student(
        self, people: Office
    ) -> None:
        request_id = people.asked(people.anjali)
        with tenant_context(people.office.consultancy):
            Staff.objects.filter(pk=people.anjali.pk).update(role=Role.FRONT_DESK)

        response = people.act(people.bikash, request_id, "approve")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == {"detail": "The person who asked can no longer counsel Students."}
        assert stored(people.office, people.sarina).assigned_counsellor_id == people.bikash.pk
        assert people.stored_request(request_id).status == TransferRequestStatus.WAITING
        assert people.history() == []


class TestDeclining:
    def test_the_counsellor_declines_with_a_reason(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(
            people.bikash, request_id, "decline", {"reason": " Let's talk after her PTE. "}
        )

        assert response.status_code == status.HTTP_200_OK
        answer = response.json()
        assert answer["status"] == "declined"
        assert answer["answer_reason"] == "Let's talk after her PTE."
        assert answer["answered_by"] == {"name": "Bikash Thapa", "is_platform_admin": False}
        assert stored(people.office, people.sarina).assigned_counsellor_id == people.bikash.pk
        assert people.history() == []
        assert people.notifications_of(people.anjali) == ["transfer_declined"]

    def test_the_reason_is_optional(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.admin, request_id, "decline")

        assert response.json()["answer_reason"] == ""

    @pytest.mark.parametrize("reason", [None, 1, False, ["x"], "x" * 501, "a\x00b"])
    def test_bad_reasons_are_refused(self, people: Office, reason: object) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.bikash, request_id, "decline", {"reason": reason})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "reason" in response.json()
        assert people.stored_request(request_id).status == TransferRequestStatus.WAITING

    @pytest.mark.parametrize("reason", INJECTION_STRINGS)
    def test_injection_shaped_reasons_are_kept_as_plain_text(
        self, people: Office, reason: str
    ) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.bikash, request_id, "decline", {"reason": reason})

        assert response.json()["answer_reason"] == reason

    def test_unknown_fields_are_refused(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.bikash, request_id, "decline", {"status": "approved"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert people.stored_request(request_id).status == TransferRequestStatus.WAITING

    def test_an_answered_request_cant_be_declined(self, people: Office) -> None:
        request_id = people.asked(people.anjali)
        people.act(people.anjali, request_id, "cancel")

        response = people.act(people.bikash, request_id, "decline")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == NOT_WAITING
        assert people.stored_request(request_id).status == TransferRequestStatus.CANCELLED

    def test_the_asker_cant_decline_their_own(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.anjali, request_id, "decline")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == OWN_REQUEST


class TestCancelling:
    def test_the_asker_cancels_while_it_waits(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.anjali, request_id, "cancel")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "cancelled"
        assert response.json()["answered_by"] is None
        assert people.stored_request(request_id).status == TransferRequestStatus.CANCELLED

    @pytest.mark.parametrize("who", ["bikash", "admin", "desk"])
    def test_nobody_else_cancels(self, people: Office, who: str) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(getattr(people, who), request_id, "cancel")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_REQUESTER

    def test_a_teacher_is_refused(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.teacher, request_id, "cancel")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == TEACHERS

    def test_an_answered_request_cant_be_cancelled(self, people: Office) -> None:
        request_id = people.asked(people.anjali)
        people.act(people.bikash, request_id, "approve")

        response = people.act(people.anjali, request_id, "cancel")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == NOT_WAITING

    def test_cancel_takes_an_empty_body(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        response = people.act(people.anjali, request_id, "cancel", {"reason": "x"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestClosingByItself:
    def test_a_transfer_made_another_way_closes_the_waiting_request(self, people: Office) -> None:
        request_id = people.asked(people.anjali)
        sunita = named_staff(people.office, "Sunita Rai")

        people.as_(people.admin).client.post(
            transfer_url(people.sarina), {"counsellor": str(sunita.public_id)}, format="json"
        )

        closed = people.stored_request(request_id)
        assert closed.status == TransferRequestStatus.CLOSED
        assert closed.answer_reason == TRANSFERRED_ANOTHER_WAY
        assert closed.answered_at is not None
        assert people.ask(people.anjali).status_code == status.HTTP_201_CREATED

    def test_assigning_a_counsellor_closes_the_waiting_request(self, people: Office) -> None:
        lead = make_student(people.office, name="Dipesh Rana")
        request_id = people.asked(people.anjali, lead)

        people.as_(people.desk).client.post(
            f"/api/students/{lead.public_id}/assign/",
            {"counsellor": str(people.bikash.public_id)},
            format="json",
        )

        assert people.stored_request(request_id).status == TransferRequestStatus.CLOSED

    def test_deactivating_the_asker_closes_their_waiting_requests(self, people: Office) -> None:
        request_id = people.asked(people.anjali)
        answered = people.asked(people.anjali, make_student(people.office, name="Dipesh Rana"))
        people.act(people.admin, answered, "decline")

        response = people.as_(people.admin).client.post(
            f"/api/staff/{people.anjali.public_id}/deactivate/", format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        closed = people.stored_request(request_id)
        assert closed.status == TransferRequestStatus.CLOSED
        assert closed.answer_reason == REQUESTER_LEFT
        assert people.stored_request(answered).status == TransferRequestStatus.DECLINED

    def test_a_closed_request_cant_be_approved_later(self, people: Office) -> None:
        request_id = people.asked(people.anjali)
        people.as_(people.admin).client.post(
            f"/api/staff/{people.anjali.public_id}/deactivate/", format="json"
        )

        response = people.act(people.bikash, request_id, "approve")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert stored(people.office, people.sarina).assigned_counsellor_id == people.bikash.pk


class TestTheLists:
    def _three(self, people: Office) -> dict[str, str]:
        """Anjali asks for Sarina; Bikash asks for Dipesh (no counsellor); Anjali for Asmita."""
        dipesh = make_student(people.office, name="Dipesh Rana")
        asmita = make_student(people.office, people.admin, name="Asmita Rai")
        return {
            "sarina": people.asked(people.anjali),
            "dipesh": people.asked(people.bikash, dipesh),
            "asmita": people.asked(people.anjali, asmita),
        }

    def _ids(self, people: Office, staff: Staff, box: str) -> list[str]:
        page = people.as_(staff).client.get(LIST_URL, {"box": box}).json()
        return [item["id"] for item in page["results"]]

    def test_waiting_for_me_holds_what_each_person_may_answer(self, people: Office) -> None:
        ids = self._three(people)

        assert self._ids(people, people.bikash, "waiting_for_me") == [ids["sarina"]]
        assert self._ids(people, people.desk, "waiting_for_me") == [ids["dipesh"]]
        assert self._ids(people, people.anjali, "waiting_for_me") == []
        assert set(self._ids(people, people.admin, "waiting_for_me")) == set(ids.values())

    def test_waiting_for_me_is_the_default(self, people: Office) -> None:
        ids = self._three(people)

        page = people.as_(people.bikash).client.get(LIST_URL).json()

        assert [item["id"] for item in page["results"]] == [ids["sarina"]]
        assert page["results"][0]["can_answer"] is True

    def test_sent_holds_mine_newest_first_answered_too(self, people: Office) -> None:
        ids = self._three(people)
        people.act(people.admin, ids["sarina"], "decline", {"reason": "Not now."})

        assert self._ids(people, people.anjali, "sent") == [ids["asmita"], ids["sarina"]]
        assert self._ids(people, people.desk, "sent") == []

    def test_everyone_is_for_admins(self, people: Office) -> None:
        ids = self._three(people)
        people.act(people.anjali, ids["asmita"], "cancel")

        assert len(self._ids(people, people.admin, "all")) == 3
        response = people.as_(people.bikash).client.get(LIST_URL, {"box": "all"})
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN_ALL

    def test_an_answered_request_keeps_who_the_student_was_with_when_asked(
        self, people: Office
    ) -> None:
        request_id = people.asked(people.anjali)
        people.act(people.bikash, request_id, "decline")
        people.as_(people.admin).client.post(
            transfer_url(people.sarina), {"counsellor": str(people.anjali.public_id)}, format="json"
        )

        [declined] = (
            people.as_(people.anjali).client.get(LIST_URL, {"box": "sent"}).json()["results"]
        )

        assert declined["taken_from"] == "Bikash Thapa"
        assert declined["counsellor"] == {
            "id": str(people.anjali.public_id),
            "name": "Anjali Gurung",
        }

    def test_answered_requests_leave_waiting_for_me(self, people: Office) -> None:
        ids = self._three(people)
        people.act(people.bikash, ids["sarina"], "approve")

        assert self._ids(people, people.bikash, "waiting_for_me") == []

    def test_the_page_has_counts_and_the_count_endpoint_matches(self, people: Office) -> None:
        self._three(people)

        page = people.as_(people.admin).client.get(LIST_URL, {"box": "all"}).json()
        count = people.office.client.get(COUNT_URL).json()

        assert (page["count"], page["page"], page["waiting_for_me_count"]) == (3, 1, 3)
        assert count == {"count": 3}
        assert people.as_(people.desk).client.get(COUNT_URL).json() == {"count": 1}
        assert people.as_(people.anjali).client.get(COUNT_URL).json() == {"count": 0}

    def test_a_teacher_is_refused_both(self, people: Office) -> None:
        client = people.as_(people.teacher).client

        assert client.get(LIST_URL).json() == TEACHERS
        assert client.get(COUNT_URL).status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize(
        "query",
        [{"box": "everyone"}, {"box": "ALL"}, {"page": "0"}, {"page": "x"}, {"sort": "id"}],
    )
    def test_bad_queries_are_refused(self, people: Office, query: dict[str, str]) -> None:
        response = people.as_(people.admin).client.get(LIST_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_page_past_the_end_is_not_found(self, people: Office) -> None:
        response = people.as_(people.admin).client.get(LIST_URL, {"page": "2"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, people: Office, method: str) -> None:
        client = people.as_(people.admin).client

        assert getattr(client, method)(LIST_URL, {}, format="json").status_code == 405
        assert getattr(client, method)(COUNT_URL, {}, format="json").status_code == 405

    def test_logged_out_is_refused(self, people: Office) -> None:
        assert people.office.new_client().get(LIST_URL).status_code == 401

    def test_a_platform_admin_sees_every_waiting_request(
        self, people: Office, platform_site: PlatformContext
    ) -> None:
        ids = self._three(people)
        visit = open_as_platform_admin(platform_site, people.office)

        page = visit.client.get(LIST_URL).json()

        assert {item["id"] for item in page["results"]} == set(ids.values())
        assert visit.client.get(LIST_URL, {"box": "sent"}).json()["count"] == 0
        assert visit.client.get(COUNT_URL).json() == {"count": 3}


class TestOnTheProfile:
    def _profile(self, people: Office, staff: Staff) -> dict[str, object]:
        response = people.as_(staff).client.get(f"/api/students/{people.sarina.public_id}/")
        answer: dict[str, object] = response.json()
        return answer

    def test_a_counsellor_on_someone_elses_student_may_ask(self, people: Office) -> None:
        profile = self._profile(people, people.anjali)

        assert profile["can_ask_transfer"] is True
        assert profile["transfer_request"] is None

    @pytest.mark.parametrize("who", ["bikash", "desk"])
    def test_their_own_counsellor_and_front_desk_may_not(self, people: Office, who: str) -> None:
        assert self._profile(people, getattr(people, who))["can_ask_transfer"] is False

    def test_the_waiting_request_shows_with_what_each_person_may_do(self, people: Office) -> None:
        request_id = people.asked(people.anjali)

        mine = self._profile(people, people.anjali)
        theirs = self._profile(people, people.bikash)
        admin = self._profile(people, people.admin)

        assert mine["can_ask_transfer"] is False
        assert mine["transfer_request"] == {
            **theirs["transfer_request"],  # type: ignore[dict-item]
            "can_answer": False,
            "can_cancel": True,
        }
        waiting = theirs["transfer_request"]
        assert isinstance(waiting, dict)
        assert (waiting["id"], waiting["status"]) == (request_id, "waiting")
        assert (waiting["can_answer"], waiting["can_cancel"]) == (True, False)
        assert admin["can_ask_transfer"] is False

    def test_an_answered_request_is_no_longer_shown(self, people: Office) -> None:
        request_id = people.asked(people.anjali)
        people.act(people.bikash, request_id, "decline")

        assert self._profile(people, people.bikash)["transfer_request"] is None
