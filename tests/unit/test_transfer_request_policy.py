"""Who may ask to take a Student, and who may answer a Transfer Request (spec 44a-44c; issue 57)."""

from __future__ import annotations

import pytest

from access.policy import (
    ALREADY_YOURS_MESSAGE,
    ANSWER_ASSIGNED_MESSAGE,
    ANSWER_UNASSIGNED_MESSAGE,
    ASK_ROLES_MESSAGE,
    ONLY_ADMIN_SEES_ALL_REQUESTS_MESSAGE,
    ONLY_REQUESTER_CANCELS_MESSAGE,
    OWN_REQUEST_MESSAGE,
    PLATFORM_CANT_TAKE_MESSAGE,
    TEACHERS_MESSAGE,
    why_cannot_answer_transfer,
    why_cannot_ask_transfer,
    why_cannot_cancel_transfer,
    why_cannot_see_all_transfer_requests,
)
from staff.models import PLATFORM_VISITOR_ID, PlatformVisitor, Role, Staff, StaffStatus
from students.models import Stage, Student

ME = 7
THEIR_COUNSELLOR = 8
ASKER = 9


def person(role: Role, status: StaffStatus = StaffStatus.ACTIVE, pk: int = ME) -> Staff:
    """A Staff member, not stored."""
    return Staff(pk=pk, name="Sita", role=role, status=status)


def visitor() -> PlatformVisitor:
    """A Platform Admin inside an office."""
    return PlatformVisitor(
        pk=PLATFORM_VISITOR_ID, name="Ramesh", role=Role.ADMIN, status=StaffStatus.ACTIVE
    )


def student(counsellor_id: int | None, stage: Stage = Stage.COUNSELLING) -> Student:
    """A Student, not stored."""
    return Student(pk=1, name="Sarina", assigned_counsellor_id=counsellor_id, stage=stage)


class TestWhoAsks:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.ADMIN])
    def test_a_counsellor_or_admin_asks_for_someone_elses_student(self, role: Role) -> None:
        assert why_cannot_ask_transfer(person(role), student(THEIR_COUNSELLOR)) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.ADMIN])
    def test_and_for_a_student_with_no_counsellor(self, role: Role) -> None:
        assert why_cannot_ask_transfer(person(role), student(None)) is None

    def test_a_lost_student_may_be_asked_for(self) -> None:
        lost = student(THEIR_COUNSELLOR, Stage.LOST)
        assert why_cannot_ask_transfer(person(Role.COUNSELLOR), lost) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.ADMIN])
    def test_not_for_their_own_student(self, role: Role) -> None:
        assert why_cannot_ask_transfer(person(role), student(ME)) == ALREADY_YOURS_MESSAGE

    def test_front_desk_never_asks(self) -> None:
        assert why_cannot_ask_transfer(person(Role.FRONT_DESK), student(None)) == (
            ASK_ROLES_MESSAGE
        )

    def test_a_teacher_never_asks(self) -> None:
        assert why_cannot_ask_transfer(person(Role.TEACHER), student(None)) == TEACHERS_MESSAGE

    @pytest.mark.parametrize("status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_nobody_who_isnt_active(self, status: StaffStatus) -> None:
        asker = person(Role.COUNSELLOR, status)
        assert why_cannot_ask_transfer(asker, student(None)) == ASK_ROLES_MESSAGE

    def test_a_platform_admin_never_takes_a_student(self) -> None:
        assert why_cannot_ask_transfer(visitor(), student(None)) == PLATFORM_CANT_TAKE_MESSAGE


class TestWhoAnswers:
    def test_the_students_counsellor_answers(self) -> None:
        mine = student(ME)
        assert why_cannot_answer_transfer(person(Role.COUNSELLOR), mine, ASKER) is None

    @pytest.mark.parametrize("counsellor_id", [THEIR_COUNSELLOR, None])
    def test_an_admin_answers_any(self, counsellor_id: int | None) -> None:
        assert why_cannot_answer_transfer(person(Role.ADMIN), student(counsellor_id), ASKER) is None

    def test_a_platform_admin_answers_as_an_admin(self) -> None:
        assert why_cannot_answer_transfer(visitor(), student(THEIR_COUNSELLOR), ASKER) is None

    def test_another_counsellor_cant(self) -> None:
        theirs = student(THEIR_COUNSELLOR)
        assert why_cannot_answer_transfer(person(Role.COUNSELLOR), theirs, ASKER) == (
            ANSWER_ASSIGNED_MESSAGE
        )

    def test_front_desk_answers_only_for_a_student_with_no_counsellor(self) -> None:
        desk = person(Role.FRONT_DESK)
        assert why_cannot_answer_transfer(desk, student(None), ASKER) is None
        assert why_cannot_answer_transfer(desk, student(THEIR_COUNSELLOR), ASKER) == (
            ANSWER_ASSIGNED_MESSAGE
        )

    def test_a_counsellor_cant_answer_for_a_student_with_no_counsellor(self) -> None:
        assert why_cannot_answer_transfer(person(Role.COUNSELLOR), student(None), ASKER) == (
            ANSWER_UNASSIGNED_MESSAGE
        )

    def test_a_teacher_never_answers(self) -> None:
        assert why_cannot_answer_transfer(person(Role.TEACHER), student(None), ASKER) == (
            TEACHERS_MESSAGE
        )

    def test_nobody_answers_their_own_request(self) -> None:
        admin = person(Role.ADMIN)
        assert why_cannot_answer_transfer(admin, student(None), ME) == OWN_REQUEST_MESSAGE

    def test_someone_who_has_left_cant(self) -> None:
        left = person(Role.ADMIN, StaffStatus.DEACTIVATED)
        assert why_cannot_answer_transfer(left, student(None), ASKER) == ANSWER_ASSIGNED_MESSAGE

    def test_a_platform_admin_is_never_the_asker_even_with_the_same_id(self) -> None:
        assert why_cannot_answer_transfer(visitor(), student(None), PLATFORM_VISITOR_ID) is None


class TestWhoCancels:
    def test_only_the_asker(self) -> None:
        assert why_cannot_cancel_transfer(person(Role.COUNSELLOR), ME) is None
        assert why_cannot_cancel_transfer(person(Role.ADMIN), ASKER) == (
            ONLY_REQUESTER_CANCELS_MESSAGE
        )

    def test_never_a_platform_admin(self) -> None:
        assert why_cannot_cancel_transfer(visitor(), PLATFORM_VISITOR_ID) == (
            ONLY_REQUESTER_CANCELS_MESSAGE
        )


class TestWhoSeesEveryonesRequests:
    def test_admins_and_platform_admins(self) -> None:
        assert why_cannot_see_all_transfer_requests(person(Role.ADMIN)) is None
        assert why_cannot_see_all_transfer_requests(visitor()) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_no_other_role(self, role: Role) -> None:
        assert why_cannot_see_all_transfer_requests(person(role)) == (
            ONLY_ADMIN_SEES_ALL_REQUESTS_MESSAGE
        )
