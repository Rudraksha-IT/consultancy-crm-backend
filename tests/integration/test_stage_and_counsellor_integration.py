"""Manual Stage, Lost, assigning a Counsellor and Transfer (spec 38-44; issue 11)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, VisaStatus
from catalogue.models import Course, CourseLevel, University
from history.models import HistoryEntry, HistoryKind
from staff.models import Role, Staff, StaffStatus
from staff.services import create_staff
from staff.types import NewStaff
from students.exceptions import StudentChangedError
from students.journey import assign_counsellor, transfer_student
from students.models import Source, Stage, Student
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

NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
NOT_YOUR_STUDENT = {
    "detail": "Not your Student. Only their counsellor or an Admin can change them."
}
COUNSELLOR_OR_ADMIN = {"detail": "Only the Student's counsellor or an Admin can do this."}
ASSIGN_ROLES = {"detail": "Only an Admin or Front Desk can assign a counsellor."}
ALREADY_ASSIGNED = {"detail": "This Student already has a counsellor. Transfer them instead."}
NOT_ASSIGNED_YET = {"detail": "This Student has no counsellor yet. Assign one instead."}
AUTOMATIC = (
    "Applying, Offer and Visa follow from Applications, offer letters and visas. "
    "They can't be set by hand."
)
CHOOSE_COUNSELLOR = {"counsellor": ["Choose a counsellor from the list."]}
LOST_REASON_NEEDED = {"lost_reason": ["Say why we lost this Student."]}
ONLY_LOST_HAS_A_REASON = {"lost_reason": ["Only a Lost Student has a reason."]}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE students_student; --",
    "}body{background:url(javascript:alert(1))}",
]


def stage_url(student: Student | int | str) -> str:
    """The Student's Stage endpoint."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/stage/"


def assign_url(student: Student | int | str) -> str:
    """The endpoint that assigns a Student their first counsellor."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/assign/"


def transfer_url(student: Student | int | str) -> str:
    """The Student's Transfer endpoint."""
    pk = student if isinstance(student, int | str) else student.public_id
    return f"/api/students/{pk}/transfer/"


def assigned_url(staff: Staff | int) -> str:
    """The Students still assigned to a Staff member."""
    pk = staff if isinstance(staff, int | str) else staff.public_id
    return f"/api/staff/{pk}/assigned-students/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def named_staff(office: ConsultancyContext, name: str, role: Role = Role.COUNSELLOR) -> Staff:
    """An active Staff member with this name."""
    email = f"{name.split(maxsplit=1)[0].lower()}@{office.host}"
    with tenant_context(office.consultancy):
        return create_staff(NewStaff(name=name, email=email, role=role), password=TEST_PASSWORD)


def log_in(office: ConsultancyContext, staff: Staff) -> None:
    """Log the office's client in as `staff`."""
    use_access_token(office.client, office.tokens_for(staff).access)


def make_student(
    office: ConsultancyContext,
    counsellor: Staff | None = None,
    stage: Stage = Stage.LEAD,
    **fields: object,
) -> Student:
    """Store a Student directly; `fields` override the defaults."""
    details: dict[str, object] = {
        "name": "Sita Gurung",
        "phone": "9841000001",
        "country": "australia",
        "source": Source.WALK_IN,
        "highest_qualification": "plus_two",
        **fields,
    }
    with tenant_context(office.consultancy):
        return Student.objects.create(assigned_counsellor=counsellor, stage=stage, **details)


def stored(office: ConsultancyContext, student: Student) -> Student:
    """The Student as stored now."""
    with tenant_context(office.consultancy):
        return Student.objects.select_related("assigned_counsellor").get(pk=student.pk)


def history_rows(office: ConsultancyContext, student: Student) -> list[HistoryEntry]:
    """The Student's stored History, oldest first."""
    with tenant_context(office.consultancy):
        return list(HistoryEntry.objects.filter(student=student).order_by("at", "pk"))


def set_stage(
    office: ConsultancyContext, student: Student, **body: object
) -> _MonkeyPatchedResponse:
    """POST a Stage change."""
    return office.client.post(stage_url(student), body, format="json")


def assign(
    office: ConsultancyContext, student: Student, counsellor: object
) -> _MonkeyPatchedResponse:
    """POST an assignment."""
    return office.client.post(assign_url(student), {"counsellor": counsellor}, format="json")


def transfer(
    office: ConsultancyContext, student: Student, counsellor: object
) -> _MonkeyPatchedResponse:
    """POST a Transfer."""
    return office.client.post(transfer_url(student), {"counsellor": counsellor}, format="json")


class TestSettingTheStageByHand:
    def test_the_assigned_counsellor_moves_their_student_on(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me)

        response = set_stage(abc, student, stage="counselling")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["stage"] == "counselling"
        assert stored(abc, student).stage == Stage.COUNSELLING

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (Stage.LEAD, Stage.COUNSELLING),
            (Stage.COUNSELLING, Stage.LEAD),
        ],
    )
    def test_lead_and_counselling_move_both_ways(
        self, abc: ConsultancyContext, current: Stage, target: Stage
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, current)

        assert set_stage(abc, student, stage=target.value).json()["stage"] == target.value

    def test_an_admin_can_move_anyones_student(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other, Stage.COUNSELLING)
        abc.log_in_as(Role.ADMIN)

        assert set_stage(abc, student, stage="lead").status_code == status.HTTP_200_OK

    def test_the_change_is_recorded_in_history(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        student = make_student(abc, me)

        set_stage(abc, student, stage="counselling")

        [entry] = history_rows(abc, student)
        assert entry.kind == HistoryKind.STAGE_CHANGED
        assert (entry.before, entry.after, entry.detail) == ("lead", "counselling", "")
        assert entry.by_name == "Bikash Thapa"
        page = abc.client.get(f"/api/students/{student.public_id}/history/").json()
        assert page["results"][0]["kind"] == "stage_changed"
        assert page["results"][0]["detail"] == ""

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        student = make_student(abc, other)
        abc.log_in_as(Role.COUNSELLOR)

        response = set_stage(abc, student, stage="counselling")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert stored(abc, student).stage == Stage.LEAD

    def test_front_desk_is_refused_even_for_an_unassigned_lead(
        self, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        response = set_stage(abc, student, stage="counselling")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == COUNSELLOR_OR_ADMIN

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = set_stage(abc, student, stage="counselling")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_TEACHERS

    @pytest.mark.parametrize("target", ["applying", "offer", "visa"])
    def test_stages_that_follow_applications_are_refused(
        self, abc: ConsultancyContext, target: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.COUNSELLING)

        response = set_stage(abc, student, stage=target)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"stage": [AUTOMATIC]}
        assert stored(abc, student).stage == Stage.COUNSELLING
        assert history_rows(abc, student) == []

    @pytest.mark.parametrize("target", ["onboarded", "completed", "Lead", "draft"])
    def test_stages_that_do_not_exist_are_refused(
        self, abc: ConsultancyContext, target: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.COUNSELLING)

        response = set_stage(abc, student, stage=target)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["stage"]
        assert stored(abc, student).stage == Stage.COUNSELLING

    def test_a_student_applying_or_further_is_not_moved_back_by_hand(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.APPLYING)

        response = set_stage(abc, student, stage="counselling")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, student).stage == Stage.APPLYING

    def test_moving_to_the_same_stage_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = set_stage(abc, student, stage="lead")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"stage": ["The Student is already at this Stage."]}

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"stage": None},
            {"stage": 1},
            {"stage": ["lead"]},
            {"stage": "Counselling"},
            {"stage": "archived"},
            {"stage": ""},
        ],
    )
    def test_bad_stages_are_refused(self, abc: ConsultancyContext, body: dict[str, object]) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(stage_url(student), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "stage" in response.json()
        assert stored(abc, student).stage == Stage.LEAD

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = set_stage(abc, student, stage="counselling", assigned_counsellor=1, note="x")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored(abc, student).stage == Stage.LEAD

    def test_a_body_that_is_not_an_object_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = abc.client.post(stage_url(student), ["lost"], format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = getattr(abc.client, method)(stage_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_student_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(stage_url(999_999), {"stage": "lead"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = abc.new_client().post(stage_url(student), {"stage": "lost"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_platform_host_has_no_stage_endpoint(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        platform_site.log_in()

        response = platform_site.client.post(stage_url(student), {"stage": "lost"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestLost:
    def test_marking_lost_needs_a_reason_and_keeps_it(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Anjali Gurung")
        log_in(abc, me)
        student = make_student(abc, me, Stage.COUNSELLING)
        reason = "Chose a consultancy closer to home in Butwal."

        response = set_stage(abc, student, stage="lost", lost_reason=f"  {reason} ")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["stage"] == "lost"
        assert response.json()["lost_reason"] == reason
        assert stored(abc, student).lost_reason == reason
        [entry] = history_rows(abc, student)
        assert (entry.before, entry.after, entry.detail) == ("counselling", "lost", reason)

    @pytest.mark.parametrize("current", [Stage.APPLYING, Stage.OFFER, Stage.VISA])
    def test_a_student_applying_or_further_can_be_marked_lost(
        self, abc: ConsultancyContext, current: Stage
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=current)

        response = set_stage(abc, student, stage="lost", lost_reason="Decided not to go abroad.")

        assert response.json()["stage"] == "lost"

    @pytest.mark.parametrize("body", [{}, {"lost_reason": ""}, {"lost_reason": "   "}])
    def test_lost_without_a_reason_is_refused(
        self, abc: ConsultancyContext, body: dict[str, object]
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.COUNSELLING)

        response = set_stage(abc, student, stage="lost", **body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == LOST_REASON_NEEDED
        assert stored(abc, student).stage == Stage.COUNSELLING

    def test_a_reason_is_refused_for_any_other_stage(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = set_stage(abc, student, stage="counselling", lost_reason="Moved away.")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONLY_LOST_HAS_A_REASON

    @pytest.mark.parametrize("reason", [None, 42, True, ["x"], "x" * 501, "Moved\x00away"])
    def test_bad_reasons_are_refused(self, abc: ConsultancyContext, reason: object) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.COUNSELLING)

        response = set_stage(abc, student, stage="lost", lost_reason=reason)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "lost_reason" in response.json()
        assert stored(abc, student).stage == Stage.COUNSELLING

    def test_a_reason_of_five_hundred_characters_with_line_breaks_is_kept(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.COUNSELLING)
        reason = "Went abroad\nwith family." + "x" * 476

        response = set_stage(abc, student, stage="lost", lost_reason=reason)

        assert response.json()["lost_reason"] == reason

    @pytest.mark.parametrize("reason", INJECTION_STRINGS)
    def test_injection_shaped_reasons_are_kept_as_plain_text(
        self, abc: ConsultancyContext, reason: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.COUNSELLING)

        assert set_stage(abc, student, stage="lost", lost_reason=reason).json()["lost_reason"] == (
            reason
        )

    def test_marking_a_lost_student_lost_again_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.LOST, lost_reason="Moved away.")

        response = set_stage(abc, student, stage="lost", lost_reason="Again.")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, student).lost_reason == "Moved away."

    def test_a_lost_student_comes_back_to_counselling_with_full_history(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        set_stage(abc, student, stage="lost", lost_reason="Went quiet for months.")

        response = set_stage(abc, student, stage="counselling")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["stage"] == "counselling"
        assert response.json()["lost_reason"] == ""
        steps = [(e.before, e.after, e.detail) for e in history_rows(abc, student)]
        assert steps == [
            ("counselling", "lost", "Went quiet for months."),
            ("lost", "counselling", ""),
        ]

    @pytest.mark.parametrize("target", ["lead"])
    def test_a_lost_student_comes_back_only_to_counselling(
        self, abc: ConsultancyContext, target: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.LOST, lost_reason="Moved away.")

        response = set_stage(abc, student, stage=target)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"stage": ["A Lost Student can only come back to Counselling."]}

    def test_the_reason_is_never_logged(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, stage=Stage.COUNSELLING)

        set_stage(abc, student, stage="lost", lost_reason="Father lost his job in Qatar")

        assert "Qatar" not in json_logs.text()
        assert json_logs.with_message("Stage changed")


class TestTheProfileSaysWhatTheViewerMayDo:
    def test_the_profile_has_the_lost_reason_and_the_action_flags(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.LOST, lost_reason="Moved away.")

        body = abc.client.get(f"/api/students/{student.public_id}/").json()

        assert body["lost_reason"] == "Moved away."
        assert (body["can_set_stage"], body["can_assign"], body["can_transfer"]) == (
            True,
            False,
            True,
        )

    def test_front_desk_may_only_assign_an_unassigned_student(
        self, abc: ConsultancyContext
    ) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.FRONT_DESK)

        body = abc.client.get(f"/api/students/{student.public_id}/").json()

        assert (body["can_set_stage"], body["can_assign"], body["can_transfer"]) == (
            False,
            True,
            False,
        )

    def test_another_counsellor_may_do_none_of_it(self, abc: ConsultancyContext) -> None:
        other = abc.add_staff(Role.COUNSELLOR, email="other@abc.test")
        assigned = make_student(abc, other)
        unassigned = make_student(abc, name="Ram Thapa")
        abc.log_in_as(Role.COUNSELLOR)

        for student in (assigned, unassigned):
            body = abc.client.get(f"/api/students/{student.public_id}/").json()
            assert (body["can_set_stage"], body["can_assign"], body["can_transfer"]) == (
                False,
                False,
                False,
            )


class TestAssigningACounsellor:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.FRONT_DESK])
    def test_admin_and_front_desk_assign_an_unassigned_student(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(role)
        student = make_student(abc)

        response = assign(abc, student, str(bikash.public_id))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["assigned_counsellor"] == {
            "id": str(bikash.public_id),
            "name": "Bikash Thapa",
        }
        assert stored(abc, student).assigned_counsellor_id == bikash.pk

    def test_the_assignment_is_recorded_in_history(self, abc: ConsultancyContext) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        log_in(abc, desk)
        student = make_student(abc)

        assign(abc, student, str(bikash.public_id))

        [entry] = history_rows(abc, student)
        assert entry.kind == HistoryKind.COUNSELLOR_ASSIGNED
        assert (entry.before, entry.after, entry.by_name) == ("", "Bikash Thapa", "Ramesh KC")

    def test_an_admin_can_be_the_counsellor(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        assert assign(abc, student, str(admin.public_id)).status_code == status.HTTP_200_OK

    def test_front_desk_can_no_longer_change_the_student_afterwards(
        self, abc: ConsultancyContext
    ) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc)

        body = assign(abc, student, str(bikash.public_id)).json()

        assert body["can_change"] is False
        assert body["can_assign"] is False

    def test_a_counsellor_cannot_claim_a_student(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc)

        response = assign(abc, student, str(me.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ASSIGN_ROLES
        assert stored(abc, student).assigned_counsellor_id is None

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.TEACHER)
        student = make_student(abc)

        assert assign(abc, student, str(bikash.public_id)).json() == NOT_FOR_TEACHERS

    def test_a_student_who_has_a_counsellor_is_transferred_instead(
        self, abc: ConsultancyContext
    ) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        anjali = named_staff(abc, "Anjali Gurung")
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, bikash)

        response = assign(abc, student, str(anjali.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ALREADY_ASSIGNED
        assert stored(abc, student).assigned_counsellor_id == bikash.pk

    def test_only_active_counsellors_and_admins_can_be_chosen(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        teacher = named_staff(abc, "Sunita Rai", Role.TEACHER)
        left = named_staff(abc, "Nabin Adhikari")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=left.pk).update(status=StaffStatus.DEACTIVATED)
        student = make_student(abc)

        for staff in (desk, teacher, left):
            response = assign(abc, student, str(staff.public_id))
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.json() == CHOOSE_COUNSELLOR
        assert stored(abc, student).assigned_counsellor_id is None

    @pytest.mark.parametrize("counsellor", [None, "1", 1.5, True, [1], 999_999, -1, "<b>1</b>"])
    def test_bad_counsellors_are_refused(self, abc: ConsultancyContext, counsellor: object) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = assign(abc, student, counsellor)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "counsellor" in response.json()

    def test_the_counsellor_is_needed_and_nothing_else_is_allowed(
        self, abc: ConsultancyContext
    ) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        missing = abc.client.post(assign_url(student), {}, format="json")
        extra = abc.client.post(
            assign_url(student),
            {"counsellor": str(bikash.public_id), "stage": "visa"},
            format="json",
        )

        assert missing.status_code == status.HTTP_400_BAD_REQUEST
        assert set(missing.json()) == {"counsellor"}
        assert extra.status_code == status.HTTP_400_BAD_REQUEST
        assert extra.json() == {"detail": ["Unknown field."]}
        assert stored(abc, student).stage == Stage.LEAD

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = getattr(abc.client, method)(assign_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_a_student_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)

        response = abc.client.post(
            assign_url(999_999), {"counsellor": str(admin.public_id)}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestTransfer:
    def test_the_assigned_counsellor_transfers_and_loses_edit_rights(
        self, abc: ConsultancyContext
    ) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        bikash = named_staff(abc, "Bikash Thapa")
        log_in(abc, nabin)
        student = make_student(abc, nabin, Stage.COUNSELLING)

        response = transfer(abc, student, str(bikash.public_id))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["assigned_counsellor"] == {
            "id": str(bikash.public_id),
            "name": "Bikash Thapa",
        }
        assert response.json()["can_change"] is False
        note = abc.client.put(
            f"/api/students/{student.public_id}/note/", {"note": "x"}, format="json"
        )
        assert note.status_code == status.HTTP_403_FORBIDDEN
        assert transfer(abc, student, str(nabin.public_id)).json() == NOT_YOUR_STUDENT
        log_in(abc, bikash)
        assert set_stage(abc, student, stage="lead").status_code == status.HTTP_200_OK

    def test_the_transfer_is_recorded_in_history(self, abc: ConsultancyContext) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        bikash = named_staff(abc, "Bikash Thapa")
        log_in(abc, nabin)
        student = make_student(abc, nabin, Stage.COUNSELLING)

        transfer(abc, student, str(bikash.public_id))

        [entry] = history_rows(abc, student)
        assert entry.kind == HistoryKind.TRANSFERRED
        assert (entry.before, entry.after, entry.by_name) == (
            "Nabin Adhikari",
            "Bikash Thapa",
            "Nabin Adhikari",
        )

    def test_an_admin_can_transfer_anyones_student(self, abc: ConsultancyContext) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, nabin, Stage.APPLYING)

        assert transfer(abc, student, str(bikash.public_id)).status_code == status.HTTP_200_OK
        assert stored(abc, student).assigned_counsellor_id == bikash.pk

    def test_another_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, nabin)

        response = transfer(abc, student, str(me.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_YOUR_STUDENT
        assert stored(abc, student).assigned_counsellor_id == nabin.pk

    def test_front_desk_is_refused(self, abc: ConsultancyContext) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.FRONT_DESK)
        student = make_student(abc, nabin)

        assert transfer(abc, student, str(bikash.public_id)).json() == COUNSELLOR_OR_ADMIN

    def test_a_teacher_is_refused(self, abc: ConsultancyContext) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.TEACHER)
        student = make_student(abc, nabin)

        assert transfer(abc, student, str(bikash.public_id)).json() == NOT_FOR_TEACHERS

    def test_a_student_with_no_counsellor_is_assigned_instead(
        self, abc: ConsultancyContext
    ) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = transfer(abc, student, str(bikash.public_id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_ASSIGNED_YET

    def test_transferring_to_the_same_counsellor_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me)

        response = transfer(abc, student, str(me.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"counsellor": ["They're already this Student's counsellor."]}
        assert history_rows(abc, student) == []

    @pytest.mark.parametrize("counsellor", [None, "2", True, 999_999])
    def test_bad_counsellors_are_refused(self, abc: ConsultancyContext, counsellor: object) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me)

        response = transfer(abc, student, counsellor)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc, student).assigned_counsellor_id == me.pk

    def test_a_deactivated_counsellor_cannot_be_chosen(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        left = named_staff(abc, "Nabin Adhikari")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=left.pk).update(status=StaffStatus.DEACTIVATED)
        student = make_student(abc, me)

        assert transfer(abc, student, str(left.public_id)).json() == CHOOSE_COUNSELLOR

    def test_unknown_fields_are_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        bikash = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, me)

        response = abc.client.post(
            transfer_url(student),
            {"counsellor": str(bikash.public_id), "reason": "x"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_only_post_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc)

        response = getattr(abc.client, method)(transfer_url(student), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_names_are_never_logged(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, nabin)

        transfer(abc, student, str(bikash.public_id))

        assert "Bikash" not in json_logs.text()
        assert json_logs.with_message("Student transferred")


def give_visa(office: ConsultancyContext, student: Student, visa_status: VisaStatus) -> None:
    """Store a Visa at `visa_status` on a new Application at Offer for the Student."""
    with tenant_context(office.consultancy):
        university, _ = University.objects.get_or_create(
            name="Deakin University", defaults={"country": "australia"}
        )
        course = Course.objects.create(
            university=university, name=f"Course {Course.objects.count()}", level=CourseLevel.MASTER
        )
        application = Application.objects.create(
            student=student, course=course, intake="2027-03", status=ApplicationStatus.OFFER
        )
        Visa.objects.create(student=student, application=application, status=visa_status)


class TestStudentsStillAssigned:
    """Before deactivating someone, the Admin sees whom they still counsel (spec 21)."""

    def test_an_admin_sees_the_open_students_of_a_counsellor_by_name(
        self, abc: ConsultancyContext
    ) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        other = named_staff(abc, "Bikash Thapa")
        make_student(abc, nabin, Stage.APPLYING, name="Sita Karki")
        make_student(abc, nabin, Stage.LEAD, name="Rohan Bhandari")
        make_student(abc, nabin, Stage.LOST, name="Suman Gautam", lost_reason="Moved away.")
        granted = make_student(abc, nabin, Stage.VISA, name="Prakriti Maharjan")
        waiting = make_student(abc, nabin, Stage.VISA, name="Asmita Gurung")
        give_visa(abc, granted, VisaStatus.GRANTED)
        give_visa(abc, granted, VisaStatus.REFUSED)
        give_visa(abc, waiting, VisaStatus.APPLIED)
        make_student(abc, other, Stage.LEAD, name="Gita Rai")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(assigned_url(nabin))

        assert response.status_code == status.HTTP_200_OK
        assert [(s["name"], s["stage"]) for s in response.json()] == [
            ("Asmita Gurung", "visa"),
            ("Rohan Bhandari", "lead"),
            ("Sita Karki", "applying"),
        ]
        assert set(response.json()[0]) == {"id", "name", "stage"}

    def test_someone_with_no_students_has_an_empty_list(self, abc: ConsultancyContext) -> None:
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(assigned_url(desk)).json() == []

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_an_admin_may_look(self, abc: ConsultancyContext, role: Role) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        abc.log_in_as(role)

        response = abc.client.get(assigned_url(nabin))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == {"detail": "Only an Admin can manage Employees."}

    def test_someone_who_does_not_exist_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(assigned_url(999_999)).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_only_get_is_allowed(self, abc: ConsultancyContext, method: str) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(assigned_url(nabin), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestChangesMadeMeanwhile:
    """Two people acting at once: the second change sees the first and stops."""

    def test_assigning_a_student_someone_just_assigned_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        bikash = named_staff(abc, "Bikash Thapa")
        anjali = named_staff(abc, "Anjali Gurung")
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        seen = make_student(abc)
        with tenant_context(abc.consultancy):
            Student.objects.filter(pk=seen.pk).update(assigned_counsellor=bikash)

            with pytest.raises(StudentChangedError):
                assign_counsellor(seen, anjali, by=desk)

        assert stored(abc, seen).assigned_counsellor_id == bikash.pk
        assert history_rows(abc, seen) == []

    def test_transferring_a_student_someone_just_transferred_is_refused(
        self, abc: ConsultancyContext
    ) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        bikash = named_staff(abc, "Bikash Thapa")
        anjali = named_staff(abc, "Anjali Gurung")
        seen = make_student(abc, nabin)
        with tenant_context(abc.consultancy):
            Student.objects.filter(pk=seen.pk).update(assigned_counsellor=bikash)

            with pytest.raises(StudentChangedError):
                transfer_student(seen, anjali, by=nabin)

        assert stored(abc, seen).assigned_counsellor_id == bikash.pk

    @pytest.mark.parametrize(
        ("service", "url"), [("assign_counsellor", assign_url), ("transfer_student", transfer_url)]
    )
    def test_the_api_says_to_refresh(
        self,
        abc: ConsultancyContext,
        monkeypatch: pytest.MonkeyPatch,
        service: str,
        url: Callable[[Student], str],
    ) -> None:
        nabin = named_staff(abc, "Nabin Adhikari")
        bikash = named_staff(abc, "Bikash Thapa")
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, nabin if service == "transfer_student" else None)

        def changed(*_args: object, **_kwargs: object) -> None:
            raise StudentChangedError

        monkeypatch.setattr(f"students.journey_views.{service}", changed)

        response = abc.client.post(
            url(student), {"counsellor": str(bikash.public_id)}, format="json"
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json() == {
            "detail": "Someone else just changed this Student. Refresh the page."
        }
