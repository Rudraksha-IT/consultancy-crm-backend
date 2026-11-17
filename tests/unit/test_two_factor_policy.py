"""Who may switch Staff members' two-step login on or off and reset it (issue 62)."""

from __future__ import annotations

import pytest

from access.policy import (
    ADMIN_REQUIRES_TWO_FACTOR_MESSAGE,
    ONLY_ADMIN_TWO_FACTOR_MESSAGE,
    OWN_TWO_FACTOR_MESSAGE,
    why_cannot_manage_two_factor_of,
    why_cannot_switch_off_own_two_factor,
)
from staff.models import PLATFORM_VISITOR_ID, PlatformVisitor, Role, Staff, StaffStatus


def person(
    role: Role, pk: int = 7, status: StaffStatus = StaffStatus.ACTIVE, *, by_self: bool = False
) -> Staff:
    """A Staff member, not stored."""
    return Staff(pk=pk, name="Sita", role=role, status=status, two_factor_chosen_by_self=by_self)


def visitor() -> PlatformVisitor:
    """A Platform Admin inside an office."""
    return PlatformVisitor(
        pk=PLATFORM_VISITOR_ID, name="Ramesh", role=Role.ADMIN, status=StaffStatus.ACTIVE
    )


class TestWhoManagesSomeonesTwoStepLogin:
    def test_an_admin_may(self) -> None:
        assert why_cannot_manage_two_factor_of(person(Role.ADMIN, 1), person(Role.TEACHER)) is None

    def test_an_admin_may_for_another_admin(self) -> None:
        assert why_cannot_manage_two_factor_of(person(Role.ADMIN, 1), person(Role.ADMIN)) is None

    def test_a_platform_admin_visitor_may(self) -> None:
        assert why_cannot_manage_two_factor_of(visitor(), person(Role.ADMIN)) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_may_not(self, role: Role) -> None:
        refused = why_cannot_manage_two_factor_of(person(role, 1), person(Role.COUNSELLOR))

        assert refused == ONLY_ADMIN_TWO_FACTOR_MESSAGE

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.TEACHER])
    def test_nobody_else_may_on_themselves_either(self, role: Role) -> None:
        refused = why_cannot_manage_two_factor_of(person(role), person(role))

        assert refused == ONLY_ADMIN_TWO_FACTOR_MESSAGE

    def test_an_admin_may_not_on_themselves(self) -> None:
        admin = person(Role.ADMIN)

        assert why_cannot_manage_two_factor_of(admin, admin) == OWN_TWO_FACTOR_MESSAGE

    @pytest.mark.parametrize("status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_an_admin_who_isnt_active_may_not(self, status: StaffStatus) -> None:
        refused = why_cannot_manage_two_factor_of(person(Role.ADMIN, 1, status), person(Role.ADMIN))

        assert refused == ONLY_ADMIN_TWO_FACTOR_MESSAGE


class TestWhoSwitchesTheirOwnOff:
    def test_an_admin_may(self) -> None:
        assert why_cannot_switch_off_own_two_factor(person(Role.ADMIN)) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_someone_who_switched_it_on_themselves_may(self, role: Role) -> None:
        assert why_cannot_switch_off_own_two_factor(person(role, by_self=True)) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_someone_an_admin_requires_it_of_may_not(self, role: Role) -> None:
        refused = why_cannot_switch_off_own_two_factor(person(role))

        assert refused == ADMIN_REQUIRES_TWO_FACTOR_MESSAGE

    def test_a_platform_admin_visitor_has_none_here(self) -> None:
        assert why_cannot_switch_off_own_two_factor(visitor()) == OWN_TWO_FACTOR_MESSAGE
