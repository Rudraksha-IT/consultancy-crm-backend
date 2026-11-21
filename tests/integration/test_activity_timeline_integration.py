"""The Student's Stage changes, for the Activity timeline and the Lost ⓘ (spec 123; issue 82).

The profile answer carries every Stage change from History, oldest first, so the screen can work out
the time spent in each Stage without loading every page of History. Nothing is stored twice.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from history.models import HistoryEntry, HistoryKind
from history.services import Change, record
from staff.models import Role, Staff
from students.models import Source, Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory

INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE history_historyentry; --",
    "}body{background:url(javascript:alert(1))}",
]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def make_student(
    office: ConsultancyContext, counsellor: Staff | None = None, stage: Stage = Stage.LEAD
) -> Student:
    """Store a Student directly."""
    with tenant_context(office.consultancy):
        return Student.objects.create(
            name="Sita Gurung",
            phone="9841000001",
            country="australia",
            source=Source.WALK_IN,
            highest_qualification="plus_two",
            assigned_counsellor=counsellor,
            stage=stage,
        )


def profile(office: ConsultancyContext, student: Student | int | str) -> dict[str, object]:
    """GET the Student's profile, which must answer 200."""
    pk = student if isinstance(student, int | str) else student.public_id
    response = office.client.get(f"/api/students/{pk}/")
    assert response.status_code == status.HTTP_200_OK
    body: dict[str, object] = response.json()
    return body


def set_stage(office: ConsultancyContext, student: Student, **body: object) -> None:
    """POST a Stage change, which must succeed."""
    response = office.client.post(f"/api/students/{student.public_id}/stage/", body, format="json")
    assert response.status_code == status.HTTP_200_OK, response.json()


def changes(body: dict[str, object]) -> list[dict[str, object]]:
    """The profile's Stage changes."""
    found = body["stage_changes"]
    assert isinstance(found, list)
    return found


class TestTheProfileCarriesEveryStageChange:
    def test_a_new_student_has_none(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me)

        body = profile(abc, student)

        assert body["stage_changes"] == []
        assert isinstance(body["created_at"], str)

    def test_each_change_is_listed_oldest_first_with_who_when_and_why(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me)
        set_stage(abc, student, stage="counselling")
        set_stage(abc, student, stage="lost", lost_reason="Went quiet for months.")
        set_stage(abc, student, stage="counselling")

        found = changes(profile(abc, student))

        steps = [(c["kind"], c["before"], c["after"], c["detail"]) for c in found]
        assert steps == [
            ("stage_changed", "lead", "counselling", ""),
            ("stage_changed", "counselling", "lost", "Went quiet for months."),
            ("stage_changed", "lost", "counselling", ""),
        ]
        assert all(c["by"] == {"name": me.name, "is_platform_admin": False} for c in found)
        times = [str(c["at"]) for c in found]
        assert times == sorted(times)

    def test_the_answer_has_exactly_the_history_entry_fields(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, me)
        set_stage(abc, student, stage="counselling")

        [only] = changes(profile(abc, student))

        assert set(only) == {"id", "kind", "at", "by", "before", "after", "detail"}

    def test_other_kinds_of_history_are_left_out(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me)
        abc.client.put(
            f"/api/students/{student.public_id}/note/", {"note": "Likes Sydney."}, format="json"
        )
        set_stage(abc, student, stage="counselling")

        found = changes(profile(abc, student))

        assert [c["after"] for c in found] == ["counselling"]
        with tenant_context(abc.consultancy):
            assert HistoryEntry.objects.filter(student=student).count() == 2

    def test_changes_made_automatically_and_by_a_platform_admin_are_listed(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, me, Stage.COUNSELLING)
        with tenant_context(abc.consultancy):
            change = Change("counselling", "applying", "Application sent")
            record(student, HistoryKind.STAGE_CHANGED, me, change)

        [only] = changes(profile(abc, student))

        assert (only["before"], only["after"], only["detail"]) == (
            "counselling",
            "applying",
            "Application sent",
        )

    def test_entries_recorded_at_the_same_moment_keep_their_order(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, me, Stage.COUNSELLING)
        moment = timezone.now() - timedelta(days=3)
        with tenant_context(abc.consultancy):
            for before, after in (("counselling", "applying"), ("applying", "offer")):
                HistoryEntry.objects.create(
                    student=student,
                    kind=HistoryKind.STAGE_CHANGED,
                    at=moment,
                    by_name=me.name,
                    before=before,
                    after=after,
                )

        found = changes(profile(abc, student))

        assert [c["after"] for c in found] == ["applying", "offer"]

    @pytest.mark.parametrize("reason", INJECTION_STRINGS)
    def test_injection_shaped_reasons_come_back_as_plain_text(
        self, abc: ConsultancyContext, reason: str
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        student = make_student(abc, me, Stage.COUNSELLING)
        set_stage(abc, student, stage="lost", lost_reason=reason)

        [only] = changes(profile(abc, student))

        assert only["detail"] == reason

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_everyone_who_sees_students_gets_them(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        owner = abc.add_staff(Role.COUNSELLOR, email="owner@abc.test")
        student = make_student(abc, owner, Stage.COUNSELLING)
        with tenant_context(abc.consultancy):
            record(student, HistoryKind.STAGE_CHANGED, owner, Change("lead", "counselling"))
        abc.log_in_as(role)

        assert len(changes(profile(abc, student))) == 1

    def test_a_teacher_gets_nothing(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        abc.log_in_as(Role.TEACHER)

        response = abc.client.get(f"/api/students/{student.public_id}/")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "stage_changes" not in response.json()

    def test_logged_out_gets_nothing(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)

        response = abc.new_client().get(f"/api/students/{student.public_id}/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_many_changes_cost_no_more_queries_than_one(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        student = make_student(abc, me)
        set_stage(abc, student, stage="counselling")
        with CaptureQueriesContext(connection) as one:
            profile(abc, student)
        for stage in ("lead", "counselling") * 5:
            set_stage(abc, student, stage=stage)

        with CaptureQueriesContext(connection) as eleven:
            body = profile(abc, student)

        assert len(changes(body)) == 11
        assert len(eleven) == len(one)
