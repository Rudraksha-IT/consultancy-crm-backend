"""Who may tick a Task, and who may link one to a Student (issue 21)."""

from __future__ import annotations

import pytest

from access.policy import (
    CANT_TICK_TASK_MESSAGE,
    TEACHERS_MESSAGE,
    why_cannot_link_student,
    why_cannot_mark_task_done,
)
from staff.models import Role, Staff, StaffStatus

ASSIGNEE, GIVER, SOMEONE_ELSE = 1, 2, 3


def staff_member(role: Role, pk: int, status: StaffStatus = StaffStatus.ACTIVE) -> Staff:
    """An unsaved Staff member with this Role."""
    return Staff(pk=pk, name=f"Test {role.label}", email=f"{pk}@abc.test", role=role, status=status)


class TestMarkingDone:
    @pytest.mark.parametrize("role", list(Role))
    def test_the_assignee_may(self, role: Role) -> None:
        assert why_cannot_mark_task_done(staff_member(role, ASSIGNEE), ASSIGNEE, GIVER) is None

    @pytest.mark.parametrize("role", list(Role))
    def test_whoever_gave_it_may(self, role: Role) -> None:
        assert why_cannot_mark_task_done(staff_member(role, GIVER), ASSIGNEE, GIVER) is None

    def test_an_admin_may_tick_anyones(self) -> None:
        admin = staff_member(Role.ADMIN, SOMEONE_ELSE)

        assert why_cannot_mark_task_done(admin, ASSIGNEE, GIVER) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_nobody_else_may(self, role: Role) -> None:
        other = staff_member(role, SOMEONE_ELSE)

        assert why_cannot_mark_task_done(other, ASSIGNEE, GIVER) == CANT_TICK_TASK_MESSAGE

    @pytest.mark.parametrize("status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_someone_not_active_may_not(self, status: StaffStatus) -> None:
        admin = staff_member(Role.ADMIN, ASSIGNEE, status)

        assert why_cannot_mark_task_done(admin, ASSIGNEE, GIVER) == CANT_TICK_TASK_MESSAGE


class TestLinkingAStudent:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_roles_that_see_students_may(self, role: Role) -> None:
        assert why_cannot_link_student(staff_member(role, 1)) is None

    def test_a_teacher_may_not(self) -> None:
        assert why_cannot_link_student(staff_member(Role.TEACHER, 1)) == TEACHERS_MESSAGE
