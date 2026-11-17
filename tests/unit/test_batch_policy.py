"""Who may see and change Batches (shown as "classes"); issue 23, spec 22 and 83-84."""

from __future__ import annotations

import pytest

from access.policy import (
    BATCH_ROLES_MESSAGE,
    CHANGE_BATCHES_MESSAGE,
    PAPERWORK_ROLES_MESSAGE,
    BatchAction,
    can_see_batch,
    why_cannot_on_batches,
)
from staff.models import Role, Staff, StaffStatus

ME, SOMEONE_ELSE = 1, 2


def staff_member(role: Role, pk: int = ME, status: StaffStatus = StaffStatus.ACTIVE) -> Staff:
    """An unsaved Staff member with this Role."""
    return Staff(pk=pk, name=f"Test {role.label}", email=f"{pk}@abc.test", role=role, status=status)


class TestSeeing:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.TEACHER])
    def test_admins_counsellors_and_teachers_see_classes(self, role: Role) -> None:
        assert why_cannot_on_batches(staff_member(role), BatchAction.SEE) is None

    def test_front_desk_does_not(self) -> None:
        reason = why_cannot_on_batches(staff_member(Role.FRONT_DESK), BatchAction.SEE)

        assert reason == BATCH_ROLES_MESSAGE

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    def test_admins_and_counsellors_see_every_class(self, role: Role) -> None:
        assert can_see_batch(staff_member(role), teacher_id=SOMEONE_ELSE) is True

    def test_a_teacher_sees_only_the_classes_they_teach(self) -> None:
        teacher = staff_member(Role.TEACHER)

        assert can_see_batch(teacher, teacher_id=ME) is True
        assert can_see_batch(teacher, teacher_id=SOMEONE_ELSE) is False

    def test_front_desk_sees_no_class(self) -> None:
        assert can_see_batch(staff_member(Role.FRONT_DESK), teacher_id=ME) is False


class TestChanging:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    @pytest.mark.parametrize("action", [BatchAction.CHANGE, BatchAction.SEE_ENROLMENT])
    def test_admins_and_counsellors_may(self, role: Role, action: BatchAction) -> None:
        assert why_cannot_on_batches(staff_member(role), action) is None

    def test_a_teacher_may_not_change_classes(self) -> None:
        reason = why_cannot_on_batches(staff_member(Role.TEACHER), BatchAction.CHANGE)

        assert reason == CHANGE_BATCHES_MESSAGE

    def test_a_teacher_may_not_see_everyone_enrolled(self) -> None:
        reason = why_cannot_on_batches(staff_member(Role.TEACHER), BatchAction.SEE_ENROLMENT)

        assert reason == PAPERWORK_ROLES_MESSAGE

    @pytest.mark.parametrize("action", list(BatchAction))
    def test_front_desk_hears_that_classes_arent_for_them(self, action: BatchAction) -> None:
        reason = why_cannot_on_batches(staff_member(Role.FRONT_DESK), action)

        assert reason == BATCH_ROLES_MESSAGE


class TestNotActive:
    @pytest.mark.parametrize("status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    @pytest.mark.parametrize("action", list(BatchAction))
    def test_someone_not_active_may_do_nothing(
        self, status: StaffStatus, action: BatchAction
    ) -> None:
        admin = staff_member(Role.ADMIN, status=status)

        assert why_cannot_on_batches(admin, action) == BATCH_ROLES_MESSAGE
        assert can_see_batch(admin, teacher_id=ME) is False
