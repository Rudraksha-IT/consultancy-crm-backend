"""Public services for one Student's profile (spec 35-37). Call inside the Consultancy's schema.

Permission checks are the views' job, through `access.policy`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import UUID

from django.db import transaction
from django.db.models import F

from history.models import HistoryKind
from history.services import Change, record
from staff.models import Staff
from students.exceptions import TooManyEntriesError
from students.models import AcademicRecord, Priority, Student, TestScore, WorkExperience

# How many qualifications, or test scores, one Student can have.
MAX_ENTRIES_PER_STUDENT = 20

# The details `change_details` may change. The Note, Stage and Counsellor have their own services.
DETAIL_FIELDS = frozenset(
    {
        "name",
        "phone",
        "email",
        "country",
        "other_countries",
        "date_of_birth",
        "gender",
        "marital_status",
        "nationality",
        "father_name",
        "mother_name",
        "guardian_name",
        "guardian_relation",
        "guardian_phone",
        "passport_number",
        "passport_place_of_issue",
        "passport_issue_date",
        "passport_expiry_date",
        "mailing_same_as_permanent",
        "target_level",
        "highest_qualification",
        "past_degree",
        "previous_job",
        "preferred_course",
        "preferred_intake",
        "source",
        "sub_agent_name",
    }
)
# Each address is sent whole and stored as one column per part, e.g. `permanent_ward`.
ADDRESSES = ("permanent", "mailing")
ADDRESS_PARTS = ("province", "district", "municipality", "ward", "street")
ACADEMIC_RECORD_FIELDS = frozenset({"level", "institution", "finished_year", "result"})
TEST_SCORE_FIELDS = frozenset({"test", "overall", "taken_on", "details"})
WORK_EXPERIENCE_FIELDS = frozenset({"employer", "job_title", "started_on", "ended_on"})

logger = logging.getLogger(__name__)


def profile_of(student_id: UUID) -> Student:
    """The Student with their counsellor, academic history, test scores, Applications and Visas.

    Raises:
        Student.DoesNotExist: There is no such Student here.
    """
    return (
        Student.objects.select_related("assigned_counsellor")
        .prefetch_related(
            "academic_records",
            "test_scores",
            "work_experience",
            "applications__course__university",
            "applications__course__files",
            "applications__documents__document_type",
            "applications__documents__files",
            "visas__application__course__university",
            "visas__documents__document_type",
            "visas__documents__files",
        )
        .get(public_id=student_id)
    )


def change_details(student: Student, changes: Mapping[str, object], by: Staff) -> None:
    """Change some of the Student's details. Details aren't tracked in History (spec 45).

    An address (`permanent_address`, `mailing_address`) is a mapping of all its parts.
    """
    with transaction.atomic():
        store_details(student, changes)
        move_details_version_on(student)
    # Only ids: the details are personal data (passport, family, addresses) and never logged.
    logger.info("Student details changed: student_id=%s, by_staff_id=%s", student.pk, by.pk)


def store_details(student: Student, changes: Mapping[str, object]) -> None:
    """Store some of the Student's details, without moving their version on or logging."""
    flat = _flatten_addresses(changes)
    apply_changes(student, flat, DETAIL_FIELDS | _ADDRESS_FIELDS)
    if "email" in flat:
        student.email = student.email.strip().lower()
    student.save(update_fields=sorted(flat))


def move_details_version_on(student: Student) -> None:
    """Note that the details or lists the Edit details page shows have changed (issue 81)."""
    Student.objects.filter(pk=student.pk).update(details_version=F("details_version") + 1)
    student.refresh_from_db(fields=["details_version"])


def edit_note(student: Student, note: str, by: Staff) -> None:
    """Replace the Student's Note; History keeps the previous text (spec 37, 45)."""
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        before = locked.note
        if before == note:
            return
        locked.note = note
        locked.save(update_fields=["note"])
        record(locked, HistoryKind.NOTE_EDITED, by, Change(before=before, after=note))
    student.note = note
    logger.info("Note edited: student_id=%s, by_staff_id=%s", student.pk, by.pk)


def set_priority(student: Student, priority: Priority, by: Staff) -> None:
    """Set the Student's Priority; History keeps each real change in words (ticket 10)."""
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        before = Priority(locked.priority)
        if before == priority:
            return
        locked.priority = priority
        locked.save(update_fields=["priority"])
        record(
            locked,
            HistoryKind.PRIORITY_CHANGED,
            by,
            Change(before=str(before.label), after=str(priority.label)),
        )
    student.priority = priority
    logger.info("Priority changed: student_id=%s, by_staff_id=%s", student.pk, by.pk)


def add_academic_record(student: Student, details: Mapping[str, object]) -> AcademicRecord:
    """Add a qualification to the Student's academic history.

    Raises:
        TooManyEntriesError: The Student already has the most allowed.
    """
    if student.academic_records.count() >= MAX_ENTRIES_PER_STUDENT:
        raise TooManyEntriesError
    entry = AcademicRecord(student=student)
    apply_changes(entry, details, ACADEMIC_RECORD_FIELDS)
    _save_entry(entry)
    return entry


def change_academic_record(entry: AcademicRecord, changes: Mapping[str, object]) -> None:
    """Change some of a qualification's details."""
    apply_changes(entry, changes, ACADEMIC_RECORD_FIELDS)
    _save_entry(entry, sorted(changes))


def add_test_score(student: Student, details: Mapping[str, object]) -> TestScore:
    """Add an English test result to the Student.

    Raises:
        TooManyEntriesError: The Student already has the most allowed.
    """
    if student.test_scores.count() >= MAX_ENTRIES_PER_STUDENT:
        raise TooManyEntriesError
    score = TestScore(student=student)
    apply_changes(score, details, TEST_SCORE_FIELDS)
    _save_entry(score)
    return score


def change_test_score(score: TestScore, changes: Mapping[str, object]) -> None:
    """Change some of a test result's details."""
    apply_changes(score, changes, TEST_SCORE_FIELDS)
    _save_entry(score, sorted(changes))


def add_work_experience(student: Student, details: Mapping[str, object]) -> WorkExperience:
    """Add a job to the Student's work experience (spec 35e).

    Raises:
        TooManyEntriesError: The Student already has the most allowed.
    """
    if student.work_experience.count() >= MAX_ENTRIES_PER_STUDENT:
        raise TooManyEntriesError
    job = WorkExperience(student=student)
    apply_changes(job, details, WORK_EXPERIENCE_FIELDS)
    _save_entry(job)
    return job


def change_work_experience(job: WorkExperience, changes: Mapping[str, object]) -> None:
    """Change some of a job's details."""
    apply_changes(job, changes, WORK_EXPERIENCE_FIELDS)
    _save_entry(job, sorted(changes))


def remove_entry(entry: AcademicRecord | TestScore | WorkExperience) -> None:
    """Remove a qualification, test result or job from the Student."""
    with transaction.atomic():
        entry.delete()
        move_details_version_on(entry.student)


def _save_entry(
    entry: AcademicRecord | TestScore | WorkExperience, fields: list[str] | None = None
) -> None:
    """Save one row and move the Student's details version on, together."""
    with transaction.atomic():
        entry.save(update_fields=fields)
        move_details_version_on(entry.student)


_ADDRESS_FIELDS = frozenset(f"{kind}_{part}" for kind in ADDRESSES for part in ADDRESS_PARTS)


def _flatten_addresses(changes: Mapping[str, object]) -> dict[str, object]:
    """The changes with each address mapping spread into its own columns."""
    flat: dict[str, object] = {}
    for field, value in changes.items():
        kind = field.removesuffix("_address")
        if kind in ADDRESSES and isinstance(value, Mapping):
            flat.update({f"{kind}_{part}": value[part] for part in ADDRESS_PARTS})
        else:
            flat[field] = value
    return flat


def apply_changes(target: object, changes: Mapping[str, object], allowed: frozenset[str]) -> None:
    """Set each of `changes` on `target`; anything outside `allowed` is refused."""
    unknown = set(changes) - allowed
    if unknown:
        msg = f"These can't be changed here: {sorted(unknown)}"
        raise ValueError(msg)
    for field, value in changes.items():
        setattr(target, field, value)
