"""Who clocks, who sees and corrects everyone's hours, who asks for and manages Work From Home.

Issues 56 and 87.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from access.policy import (
    ADMIN_NEEDS_NO_WORK_FROM_HOME_MESSAGE,
    ONLY_ACTIVE_STAFF_CLOCK_MESSAGE,
    ONLY_ADMIN_WORK_FROM_HOME_MESSAGE,
    ONLY_ADMIN_WORKING_HOURS_MESSAGE,
    PLATFORM_CANT_CLOCK_MESSAGE,
    clocks_from_any_network,
    why_cannot_ask_work_from_home,
    why_cannot_clock,
    why_cannot_manage_work_from_home,
    why_cannot_manage_working_hours,
)
from clock_in.services import clock_time
from staff.models import PLATFORM_VISITOR_ID, PlatformVisitor, Role, Staff, StaffStatus


def person(role: Role, status: StaffStatus = StaffStatus.ACTIVE) -> Staff:
    """A Staff member, not stored."""
    return Staff(pk=7, name="Sita", role=role, status=status)


def visitor() -> PlatformVisitor:
    """A Platform Admin inside an office."""
    return PlatformVisitor(
        pk=PLATFORM_VISITOR_ID, name="Ramesh", role=Role.ADMIN, status=StaffStatus.ACTIVE
    )


class TestWhoClocks:
    @pytest.mark.parametrize("role", list(Role))
    def test_every_active_role_clocks(self, role: Role) -> None:
        assert why_cannot_clock(person(role)) is None

    @pytest.mark.parametrize("status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_nobody_who_isnt_active(self, status: StaffStatus) -> None:
        assert why_cannot_clock(person(Role.ADMIN, status)) == ONLY_ACTIVE_STAFF_CLOCK_MESSAGE

    def test_never_a_platform_admin(self) -> None:
        assert why_cannot_clock(visitor()) == PLATFORM_CANT_CLOCK_MESSAGE


class TestWhoManagesWorkingHours:
    def test_an_active_admin_and_a_platform_admin(self) -> None:
        assert why_cannot_manage_working_hours(person(Role.ADMIN)) is None
        assert why_cannot_manage_working_hours(visitor()) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_no_other_role(self, role: Role) -> None:
        assert why_cannot_manage_working_hours(person(role)) == ONLY_ADMIN_WORKING_HOURS_MESSAGE

    def test_not_an_admin_who_has_left(self) -> None:
        left = person(Role.ADMIN, StaffStatus.DEACTIVATED)
        assert why_cannot_manage_working_hours(left) == ONLY_ADMIN_WORKING_HOURS_MESSAGE


class TestClockTime:
    @pytest.mark.parametrize(
        ("utc", "shown"),
        [
            ("2026-11-05T03:17:00+00:00", "9:02 AM"),
            ("2026-11-05T06:15:00+00:00", "12:00 PM"),
            ("2026-11-04T18:15:00+00:00", "12:00 AM"),
            ("2026-11-05T11:45:00+00:00", "5:30 PM"),
        ],
    )
    def test_times_as_the_office_clock_reads_them(self, utc: str, shown: str) -> None:
        assert clock_time(datetime.fromisoformat(utc)) == shown


class TestWorkFromHome:
    def test_only_admins_clock_from_any_network_by_role(self) -> None:
        assert clocks_from_any_network(person(Role.ADMIN))
        for role in (Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER):
            assert not clocks_from_any_network(person(role))

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_staff_who_arent_admins_ask(self, role: Role) -> None:
        assert why_cannot_ask_work_from_home(person(role)) is None

    def test_admins_never_need_to_ask(self) -> None:
        assert why_cannot_ask_work_from_home(person(Role.ADMIN)) == (
            ADMIN_NEEDS_NO_WORK_FROM_HOME_MESSAGE
        )

    def test_a_platform_admin_or_someone_who_left_cant_ask(self) -> None:
        assert why_cannot_ask_work_from_home(visitor()) == PLATFORM_CANT_CLOCK_MESSAGE
        left = person(Role.TEACHER, StaffStatus.DEACTIVATED)
        assert why_cannot_ask_work_from_home(left) == ONLY_ACTIVE_STAFF_CLOCK_MESSAGE

    def test_admins_and_a_platform_admin_inside_manage_dates(self) -> None:
        assert why_cannot_manage_work_from_home(person(Role.ADMIN)) is None
        assert why_cannot_manage_work_from_home(visitor()) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_nobody_else_manages_them(self, role: Role) -> None:
        assert why_cannot_manage_work_from_home(person(role)) == ONLY_ADMIN_WORK_FROM_HOME_MESSAGE

    def test_an_admin_who_left_cant_manage_them(self) -> None:
        left = person(Role.ADMIN, StaffStatus.DEACTIVATED)
        assert why_cannot_manage_work_from_home(left) == ONLY_ADMIN_WORK_FROM_HOME_MESSAGE
