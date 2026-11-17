"""Who did something: Staff, or a Platform Admin during Support Access (issue 27)."""

from __future__ import annotations

from staff.actors import PLATFORM_ADMIN_SUFFIX, actor_name, is_platform_visitor, stored_staff
from staff.models import (
    PLATFORM_VISITOR_ID,
    STAFF_NAME_MAX_LENGTH,
    PlatformVisitor,
    Role,
    Staff,
    StaffStatus,
)


def visitor(name: str = "Ramesh") -> PlatformVisitor:
    """A Platform Admin inside an office, built in memory as the services see them."""
    return PlatformVisitor(
        pk=PLATFORM_VISITOR_ID, name=name, role=Role.ADMIN, status=StaffStatus.ACTIVE
    )


def staff(name: str = "Bikash Thapa") -> Staff:
    """A Staff member, not stored."""
    return Staff(pk=7, name=name, role=Role.COUNSELLOR)


class TestActors:
    def test_staff_are_linked_and_named_as_they_are(self) -> None:
        person = staff()
        assert stored_staff(person) is person
        assert actor_name(person) == "Bikash Thapa"
        assert not is_platform_visitor(person)

    def test_a_platform_admin_is_never_linked_and_is_marked(self) -> None:
        person = visitor()
        assert stored_staff(person) is None
        assert actor_name(person) == "Ramesh (Platform Admin)"
        assert is_platform_visitor(person)

    def test_a_long_platform_admin_name_still_fits_with_its_mark(self) -> None:
        name = actor_name(visitor("A" * STAFF_NAME_MAX_LENGTH))
        assert len(name) == STAFF_NAME_MAX_LENGTH
        assert name.endswith(PLATFORM_ADMIN_SUFFIX)

    def test_a_long_staff_name_is_kept_whole(self) -> None:
        assert actor_name(staff("B" * STAFF_NAME_MAX_LENGTH)) == "B" * STAFF_NAME_MAX_LENGTH
