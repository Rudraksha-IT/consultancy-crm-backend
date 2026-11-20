"""Who did each action that Staff Progress counts, and when (spec 124, 355, 358; issue 83).

An Application's Status change, a Visa's Status change, a Document checked, and a Visa date booked,
moved or cleared each add a History entry, so they show in the Student's Activity. A Task marked
done keeps who did it on the Task, and one about a Student shows in their Activity too. Names are
kept as they were; a Platform Admin in Support Access is recorded as themselves, never as Staff.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from rest_framework import status

from applications.appointments import Appointment
from applications.services import change_application_status, change_visa_dates, change_visa_status
from applications.statuses import ApplicationStatus, VisaStatus
from documents.services import check_document
from documents.verification import Verification
from history.models import HistoryEntry, HistoryKind
from platform_admin.models import SupportVisit
from platform_admin.support_access import visitor_for
from staff.models import PlatformVisitor, Role, Staff
from students.models import Stage, Student
from tasks.models import Task
from tasks.services import set_task_done
from tests.conftest import ConsultancyContext, ConsultancyFactory, PlatformContext
from tests.integration.test_applications_integration import change as change_application
from tests.integration.test_applications_integration import make_application, make_course
from tests.integration.test_document_verification_integration import judge
from tests.integration.test_documents_integration import make_document
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from tests.integration.test_tasks_integration import make_task, tick
from tests.integration.test_visas_integration import change as change_visa
from tests.integration.test_visas_integration import (
    in_kathmandu,
    local_text,
    make_visa,
    offer_application,
    set_dates,
)
from tests.support_access import open_as_platform_admin

NEW_KINDS = {
    HistoryKind.APPLICATION_STATUS_CHANGED,
    HistoryKind.VISA_STATUS_CHANGED,
    HistoryKind.TASK_DONE,
    HistoryKind.DOCUMENT_VERIFIED,
    HistoryKind.DOCUMENT_REJECTED,
    HistoryKind.VISA_DATE_CHANGED,
}
PLATFORM_NAME = "Platform Ops"
PLATFORM_MARKED_NAME = "Platform Ops (Platform Admin)"
REASON = "Only 3 months shown, need 6 months."


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def counsellor_with_student(
    office: ConsultancyContext, stage: Stage = Stage.APPLYING
) -> tuple[Staff, Student]:
    """Log in as a named Counsellor and give them a Student at `stage`."""
    me = named_staff(office, "Bikash Thapa")
    log_in(office, me)
    return me, make_student(office, me, stage)


def activity(office: ConsultancyContext, student: Student) -> list[dict[str, Any]]:
    """The Student's History through the API, newest first, only the kinds this ticket adds."""
    response = office.client.get(f"/api/students/{student.public_id}/history/")
    assert response.status_code == status.HTTP_200_OK, response.json()
    entries: list[dict[str, Any]] = response.json()["results"]
    return [entry for entry in entries if entry["kind"] in NEW_KINDS]


def what(entries: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """Each entry's kind, before, after and detail."""
    return [(e["kind"], e["before"], e["after"], e["detail"]) for e in entries]


def stored_entries(office: ConsultancyContext, student: Student) -> list[HistoryEntry]:
    """The stored entries of the kinds this ticket adds, oldest first."""
    with tenant_context(office.consultancy):
        return list(
            HistoryEntry.objects.filter(student=student, kind__in=NEW_KINDS).order_by("at", "pk")
        )


def stored_task(office: ConsultancyContext, task: Task) -> Task:
    """The Task as stored now."""
    with tenant_context(office.consultancy):
        return Task.objects.get(pk=task.pk)


def utc_text(office_moment: datetime) -> str:
    """How a Visa date is kept in History: UTC, ISO 8601."""
    return office_moment.astimezone(UTC).isoformat()


def assert_just_now(entry: dict[str, Any]) -> None:
    """The entry was made within the last minute."""
    at = datetime.fromisoformat(entry["at"])
    assert timezone.now() - timedelta(minutes=1) <= at <= timezone.now()


class TestApplicationStatusChanges:
    def test_setting_rejected_by_hand_records_who_when_and_which_university(
        self, abc: ConsultancyContext
    ) -> None:
        me, student = counsellor_with_student(abc)
        application = make_application(abc, student, make_course(abc, "Deakin University"))

        response = change_application(abc, student, application, status="rejected")

        assert response.status_code == status.HTTP_200_OK, response.json()
        [entry] = activity(abc, student)
        assert what([entry]) == [
            ("application_status_changed", "applied", "rejected", "Deakin University")
        ]
        assert entry["by"] == {"name": me.name, "is_platform_admin": False}
        assert_just_now(entry)
        [stored] = stored_entries(abc, student)
        assert stored.by_staff_id == me.pk

    def test_back_to_applied_is_recorded_too(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_with_student(abc)
        course = make_course(abc, "Monash University")
        application = make_application(abc, student, course, ApplicationStatus.REJECTED)

        change_application(abc, student, application, status="applied")

        assert what(activity(abc, student)) == [
            ("application_status_changed", "rejected", "applied", "Monash University")
        ]

    def test_a_refused_change_records_nothing(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_with_student(abc)
        application = make_application(abc, student, make_course(abc))

        response = change_application(abc, student, application, status="applied")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_entries(abc, student) == []

    def test_someone_who_may_not_change_it_records_nothing(self, abc: ConsultancyContext) -> None:
        owner = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, owner, Stage.APPLYING)
        application = make_application(abc, student, make_course(abc))
        log_in(abc, named_staff(abc, "Sunil Rai"))

        response = change_application(abc, student, application, status="rejected")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert stored_entries(abc, student) == []


class TestVisaStatusChanges:
    def test_granting_by_hand_records_who_when_and_which_university(
        self, abc: ConsultancyContext
    ) -> None:
        me, student = counsellor_with_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student, "Deakin University"))

        response = change_visa(abc, student, visa, status="granted")

        assert response.status_code == status.HTTP_200_OK, response.json()
        [entry] = activity(abc, student)
        assert what([entry]) == [("visa_status_changed", "applied", "granted", "Deakin University")]
        assert entry["by"] == {"name": me.name, "is_platform_admin": False}
        assert_just_now(entry)

    def test_refused_is_recorded_before_the_stage_moves_back(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_with_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        change_visa(abc, student, visa, status="refused")

        with tenant_context(abc.consultancy):
            kinds = list(
                HistoryEntry.objects.filter(student=student)
                .order_by("at", "pk")
                .values_list("kind", flat=True)
            )
        assert kinds.index(HistoryKind.VISA_STATUS_CHANGED) < kinds.index(HistoryKind.STAGE_CHANGED)

    def test_the_same_status_again_records_nothing(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_with_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        response = change_visa(abc, student, visa, status="applied")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_entries(abc, student) == []


class TestDocumentsChecked:
    def test_marking_correct_records_who_when_and_which_document(
        self, abc: ConsultancyContext
    ) -> None:
        me, student = counsellor_with_student(abc, Stage.COUNSELLING)
        document = make_document(abc, student, "Passport")

        response = judge(abc, student, document, verification="verified")

        assert response.status_code == status.HTTP_200_OK, response.json()
        [entry] = activity(abc, student)
        assert what([entry]) == [("document_verified", "", "Passport", "")]
        assert entry["by"] == {"name": me.name, "is_platform_admin": False}
        assert_just_now(entry)

    def test_rejecting_records_the_reason(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_with_student(abc, Stage.COUNSELLING)
        document = make_document(abc, student, "Passport")

        judge(abc, student, document, verification="rejected", reason=REASON)

        assert what(activity(abc, student)) == [("document_rejected", "", "Passport", REASON)]

    @pytest.mark.parametrize("reason", INJECTION_STRINGS)
    def test_injection_shaped_reasons_are_kept_as_plain_text(
        self, abc: ConsultancyContext, reason: str
    ) -> None:
        _, student = counsellor_with_student(abc, Stage.COUNSELLING)
        document = make_document(abc, student, "Passport")

        judge(abc, student, document, verification="rejected", reason=reason)

        [entry] = activity(abc, student)
        assert entry["detail"] == reason

    def test_a_document_already_checked_records_nothing_more(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_with_student(abc, Stage.COUNSELLING)
        document = make_document(abc, student, "Passport", Verification.VERIFIED)

        response = judge(abc, student, document, verification="rejected", reason=REASON)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_entries(abc, student) == []


class TestVisaDates:
    def test_booking_records_the_appointment_and_when(self, abc: ConsultancyContext) -> None:
        me, student = counsellor_with_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        when = in_kathmandu(3, 14, 30)

        response = set_dates(abc, student, visa, {"biometrics_at": local_text(when)})

        assert response.status_code == status.HTTP_200_OK, response.json()
        [entry] = activity(abc, student)
        assert what([entry]) == [("visa_date_changed", "", utc_text(when), "biometrics")]
        assert entry["by"] == {"name": me.name, "is_platform_admin": False}
        assert_just_now(entry)

    def test_moving_and_clearing_record_the_old_and_new_moment(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counsellor_with_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        first, later = in_kathmandu(2), in_kathmandu(4, 8)
        set_dates(abc, student, visa, {"medical_at": local_text(first)})

        set_dates(abc, student, visa, {"medical_at": local_text(later)})
        set_dates(abc, student, visa, {"medical_at": None})

        assert what(list(reversed(activity(abc, student)))) == [
            ("visa_date_changed", "", utc_text(first), "medical"),
            ("visa_date_changed", utc_text(first), utc_text(later), "medical"),
            ("visa_date_changed", utc_text(later), "", "medical"),
        ]

    def test_each_appointment_changed_at_once_gets_its_own_entry(
        self, abc: ConsultancyContext
    ) -> None:
        _, student = counsellor_with_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        body = {
            "biometrics_at": local_text(in_kathmandu(3)),
            "interview_at": local_text(in_kathmandu(9)),
        }

        set_dates(abc, student, visa, body)

        assert sorted(entry["detail"] for entry in activity(abc, student)) == [
            "biometrics",
            "interview",
        ]

    def test_sending_the_same_moment_again_records_nothing(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_with_student(abc, Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))
        when = local_text(in_kathmandu(3))
        set_dates(abc, student, visa, {"interview_at": when})

        set_dates(abc, student, visa, {"interview_at": when})
        set_dates(abc, student, visa, {"biometrics_at": None})

        assert len(stored_entries(abc, student)) == 1

    def test_a_refused_change_records_nothing(self, abc: ConsultancyContext) -> None:
        _, student = counsellor_with_student(abc, Stage.VISA)
        application = offer_application(abc, student)
        visa = make_visa(abc, student, application, VisaStatus.GRANTED)

        response = set_dates(abc, student, visa, {"medical_at": local_text(in_kathmandu(2))})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_entries(abc, student) == []


class TestTasksDone:
    def test_marking_done_keeps_who_did_it_and_when_on_the_task(
        self, abc: ConsultancyContext
    ) -> None:
        me, student = counsellor_with_student(abc, Stage.COUNSELLING)
        task = make_task(abc, me, student=student, title="Collect bank statement")

        response = tick(abc, task)

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = stored_task(abc, task)
        assert (stored.done_by_id, stored.done_by_name) == (me.pk, me.name)
        assert stored.done_at is not None
        assert timezone.now() - timedelta(minutes=1) <= stored.done_at <= timezone.now()

    def test_a_task_about_a_student_shows_in_their_activity(self, abc: ConsultancyContext) -> None:
        me, student = counsellor_with_student(abc, Stage.COUNSELLING)
        task = make_task(abc, me, student=student, title="Collect bank statement")

        tick(abc, task)

        [entry] = activity(abc, student)
        assert what([entry]) == [("task_done", "", "Collect bank statement", "")]
        assert entry["by"] == {"name": me.name, "is_platform_admin": False}
        assert_just_now(entry)

    def test_the_person_who_gave_it_can_mark_it_done_and_is_the_one_recorded(
        self, abc: ConsultancyContext
    ) -> None:
        counsellor, student = counsellor_with_student(abc, Stage.COUNSELLING)
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        task = make_task(abc, counsellor, desk, student=student)
        log_in(abc, desk)

        tick(abc, task)

        assert stored_task(abc, task).done_by_name == "Ramesh KC"

    def test_reopening_clears_who_did_it_and_the_activity_entry_stays(
        self, abc: ConsultancyContext
    ) -> None:
        me, student = counsellor_with_student(abc, Stage.COUNSELLING)
        task = make_task(abc, me, student=student)
        tick(abc, task)

        tick(abc, task, done=False)

        stored = stored_task(abc, task)
        assert (stored.done_by_id, stored.done_by_name, stored.done_at) == (None, "", None)
        assert len(stored_entries(abc, student)) == 1

    def test_ticking_a_done_task_again_changes_nothing(self, abc: ConsultancyContext) -> None:
        me, student = counsellor_with_student(abc, Stage.COUNSELLING)
        task = make_task(abc, me, student=student)
        tick(abc, task)
        first = stored_task(abc, task)
        log_in(abc, named_staff(abc, "Anita Admin", Role.ADMIN))

        tick(abc, task)

        again = stored_task(abc, task)
        assert (again.done_at, again.done_by_name) == (first.done_at, first.done_by_name)
        assert len(stored_entries(abc, student)) == 1

    def test_a_task_about_no_student_is_recorded_on_the_task_only(
        self, abc: ConsultancyContext
    ) -> None:
        me = named_staff(abc, "Bikash Thapa")
        log_in(abc, me)
        task = make_task(abc, me, only_me=True)

        tick(abc, task)

        assert stored_task(abc, task).done_by_name == me.name
        with tenant_context(abc.consultancy):
            assert not HistoryEntry.objects.filter(kind=HistoryKind.TASK_DONE).exists()

    @pytest.mark.parametrize("title", INJECTION_STRINGS)
    def test_injection_shaped_titles_are_kept_as_plain_text(
        self, abc: ConsultancyContext, title: str
    ) -> None:
        me, student = counsellor_with_student(abc, Stage.COUNSELLING)
        task = make_task(abc, me, student=student, title=title)

        tick(abc, task)

        assert [entry["after"] for entry in activity(abc, student)] == [title]

    def test_the_task_answer_is_unchanged(self, abc: ConsultancyContext) -> None:
        me, student = counsellor_with_student(abc, Stage.COUNSELLING)
        task = make_task(abc, me, student=student)

        body = tick(abc, task).json()

        assert "done_by" not in body
        assert "done_by_name" not in body


class TestNamesAreKeptAsTheyWere:
    def test_renaming_the_staff_member_later_changes_nothing(self, abc: ConsultancyContext) -> None:
        me, student = counsellor_with_student(abc, Stage.COUNSELLING)
        task = make_task(abc, me, student=student)
        tick(abc, task)
        judge(abc, student, make_document(abc, student, "Passport"), verification="verified")
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=me.pk).update(name="Bikash Thapa Magar")

        names = {entry["by"]["name"] for entry in activity(abc, student)}

        assert names == {"Bikash Thapa"}
        assert stored_task(abc, task).done_by_name == "Bikash Thapa"


@pytest.fixture
def visitor(platform_site: PlatformContext, abc: ConsultancyContext) -> PlatformVisitor:
    """A Platform Admin named "Platform Ops" inside ABC, as the office's services see them."""
    admin = platform_site.add_admin()
    visit = open_as_platform_admin(platform_site, abc, admin)
    with schema_context("public"):
        stored = SupportVisit.objects.select_related("platform_admin").get(pk=visit.visit_id)
        return visitor_for(stored)


@pytest.fixture
def inside(abc: ConsultancyContext) -> Iterator[None]:
    """Run the test body inside ABC's schema."""
    with tenant_context(abc.consultancy):
        yield


def only_entry(student: Student, kind: HistoryKind) -> HistoryEntry:
    """The Student's one stored entry of `kind`."""
    [entry] = HistoryEntry.objects.filter(student=student, kind=kind)
    return entry


def assert_by_platform_admin(entry: HistoryEntry) -> None:
    """Recorded under the Platform Admin's own name, marked, and never linked as Staff."""
    assert (entry.by_staff_id, entry.by_name, entry.by_platform_admin) == (
        None,
        PLATFORM_NAME,
        True,
    )


@pytest.mark.usefixtures("inside")
class TestPlatformAdminActionsAreTheirs:
    """A Platform Admin's action in Support Access is recorded as theirs, never a Staff member's."""

    def test_an_application_status_change(
        self, abc: ConsultancyContext, visitor: PlatformVisitor
    ) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"), Stage.APPLYING)
        application = make_application(abc, student, make_course(abc))

        change_application_status(student, application.pk, ApplicationStatus.REJECTED, visitor)

        assert_by_platform_admin(only_entry(student, HistoryKind.APPLICATION_STATUS_CHANGED))

    def test_a_visa_status_change(self, abc: ConsultancyContext, visitor: PlatformVisitor) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"), Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        change_visa_status(student, visa.pk, VisaStatus.GRANTED, visitor)

        assert_by_platform_admin(only_entry(student, HistoryKind.VISA_STATUS_CHANGED))

    def test_a_visa_date(self, abc: ConsultancyContext, visitor: PlatformVisitor) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"), Stage.VISA)
        visa = make_visa(abc, student, offer_application(abc, student))

        change_visa_dates(student, visa.pk, {Appointment.MEDICAL: in_kathmandu(2)}, visitor)

        assert_by_platform_admin(only_entry(student, HistoryKind.VISA_DATE_CHANGED))

    def test_a_document_checked(self, abc: ConsultancyContext, visitor: PlatformVisitor) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"), Stage.COUNSELLING)
        document = make_document(abc, student, "Passport")

        check_document(student, document.pk, Verification.REJECTED, visitor, REASON)

        assert_by_platform_admin(only_entry(student, HistoryKind.DOCUMENT_REJECTED))

    def test_a_task_done(self, abc: ConsultancyContext, visitor: PlatformVisitor) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, counsellor, Stage.COUNSELLING)
        task = make_task(abc, counsellor, student=student)

        set_task_done(task.pk, done=True, by=visitor)

        stored = Task.objects.get(pk=task.pk)
        assert (stored.done_by_id, stored.done_by_name) == (None, PLATFORM_MARKED_NAME)
        assert_by_platform_admin(only_entry(student, HistoryKind.TASK_DONE))
