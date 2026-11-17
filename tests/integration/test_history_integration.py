"""History is append-only and written only by services (spec 45; issue 10)."""

from __future__ import annotations

import pytest
from django_tenants.utils import tenant_context

from history.exceptions import HistoryIsAppendOnlyError
from history.models import HistoryEntry, HistoryKind
from history.services import Change, history_of, record
from staff.models import Role
from students.models import Source, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def a_student() -> Student:
    """A stored Student (call inside the Consultancy's schema)."""
    return Student.objects.create(
        name="Sita Gurung",
        phone="9841000001",
        country="australia",
        source=Source.WALK_IN,
        highest_qualification="plus_two",
    )


class TestRecording:
    def test_a_service_records_who_when_and_what(self, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        with tenant_context(abc.consultancy):
            student = a_student()

            entry = record(
                student, HistoryKind.NOTE_EDITED, by=admin, change=Change(before="a", after="b")
            )

            assert entry.pk is not None
            assert entry.by_staff_id == admin.pk
            assert entry.by_name == admin.name
            assert entry.at is not None
            assert list(history_of(student)) == [entry]

    def test_history_is_newest_first(self, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        with tenant_context(abc.consultancy):
            student = a_student()
            older = record(
                student, HistoryKind.NOTE_EDITED, by=admin, change=Change(before="a", after="b")
            )
            newer = record(
                student, HistoryKind.NOTE_EDITED, by=admin, change=Change(before="b", after="c")
            )

            assert list(history_of(student)) == [newer, older]

    def test_the_name_is_kept_when_the_staff_member_is_renamed(
        self, abc: ConsultancyContext
    ) -> None:
        admin = abc.add_staff(Role.ADMIN)
        with tenant_context(abc.consultancy):
            student = a_student()
            entry = record(
                student, HistoryKind.NOTE_EDITED, by=admin, change=Change(before="a", after="b")
            )
            admin.name = "Someone Else"
            admin.save()

            entry.refresh_from_db()
            assert entry.by_name == "Test Admin"


class TestAppendOnly:
    def test_an_entry_cannot_be_changed(self, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        with tenant_context(abc.consultancy):
            entry = record(
                a_student(), HistoryKind.NOTE_EDITED, by=admin, change=Change(before="a", after="b")
            )
            entry.after = "forged"

            with pytest.raises(HistoryIsAppendOnlyError):
                entry.save()

    def test_an_entry_cannot_be_deleted(self, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        with tenant_context(abc.consultancy):
            entry = record(
                a_student(), HistoryKind.NOTE_EDITED, by=admin, change=Change(before="a", after="b")
            )

            with pytest.raises(HistoryIsAppendOnlyError):
                entry.delete()
            assert HistoryEntry.objects.count() == 1

    def test_entries_cannot_be_changed_or_deleted_in_bulk(self, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        with tenant_context(abc.consultancy):
            record(
                a_student(), HistoryKind.NOTE_EDITED, by=admin, change=Change(before="a", after="b")
            )

            with pytest.raises(HistoryIsAppendOnlyError):
                HistoryEntry.objects.all().update(after="forged")
            with pytest.raises(HistoryIsAppendOnlyError):
                HistoryEntry.objects.all().delete()
            assert HistoryEntry.objects.get().after == "b"
