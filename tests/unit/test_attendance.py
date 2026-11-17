"""Attendance: who may take it, and the share of class days attended (issue 24, spec 85-86)."""

from __future__ import annotations

import pytest

from access.policy import (
    BATCH_ROLES_MESSAGE,
    TAKE_ATTENDANCE_MESSAGE,
    why_cannot_take_attendance,
)
from batches.attendance import attended_percent, average_percent
from staff.models import Role, StaffStatus
from tests.unit.test_batch_policy import ME, SOMEONE_ELSE, staff_member


class TestTakingAttendance:
    def test_the_class_teacher_may(self) -> None:
        assert why_cannot_take_attendance(staff_member(Role.TEACHER), teacher_id=ME) is None

    def test_any_admin_may(self) -> None:
        assert why_cannot_take_attendance(staff_member(Role.ADMIN), teacher_id=SOMEONE_ELSE) is None

    def test_another_teacher_may_not(self) -> None:
        reason = why_cannot_take_attendance(staff_member(Role.TEACHER), teacher_id=SOMEONE_ELSE)

        assert reason == TAKE_ATTENDANCE_MESSAGE

    def test_a_counsellor_may_not(self) -> None:
        reason = why_cannot_take_attendance(staff_member(Role.COUNSELLOR), teacher_id=ME)

        assert reason == TAKE_ATTENDANCE_MESSAGE

    def test_front_desk_may_not(self) -> None:
        reason = why_cannot_take_attendance(staff_member(Role.FRONT_DESK), teacher_id=ME)

        assert reason == BATCH_ROLES_MESSAGE

    @pytest.mark.parametrize("status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_nobody_who_isnt_active_may(self, status: StaffStatus) -> None:
        for role in (Role.ADMIN, Role.TEACHER):
            reason = why_cannot_take_attendance(staff_member(role, status=status), teacher_id=ME)

            assert reason == BATCH_ROLES_MESSAGE


class TestAttendedPercent:
    def test_no_marked_day_has_no_share(self) -> None:
        assert attended_percent(attended=0, marked=0) is None

    @pytest.mark.parametrize(
        ("attended", "marked", "percent"),
        [(0, 3, 0), (3, 3, 100), (1, 2, 50), (2, 3, 67), (1, 3, 33), (1, 8, 13), (7, 8, 88)],
    )
    def test_the_share_is_rounded_half_up(self, attended: int, marked: int, percent: int) -> None:
        assert attended_percent(attended=attended, marked=marked) == percent

    def test_halves_round_up(self) -> None:
        # 1/200 is 0.5%: half up gives 1, where Python's round would give 0.
        assert attended_percent(attended=1, marked=200) == 1
        assert attended_percent(attended=5, marked=200) == 3


class TestAveragePercent:
    def test_nothing_marked_anywhere_has_no_average(self) -> None:
        assert average_percent([]) is None
        assert average_percent([None, None]) is None

    def test_classes_without_marks_are_left_out(self) -> None:
        assert average_percent([80, None, 60]) == 70

    def test_the_average_is_rounded_half_up(self) -> None:
        assert average_percent([50, 51]) == 51
        assert average_percent([100, 67, 67]) == 78
