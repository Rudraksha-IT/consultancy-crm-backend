"""Applications as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

from applications.appointments import next_appointment
from applications.models import Application, Visa
from course_files.answers import live_files_answer

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from applications.appointments import Booked
    from applications.types import FilterChoices, VisaFilterChoices
    from documents.checklist import Checklist
    from documents.models import Document
    from staff.models import Staff

# The Document Type an Offer Letter is kept as, in lower case (documents.offer_letters).
_OFFER_LETTER_NAME = "offer letter"
# How a Visa appointment is written: the Consultancy's own clock, "YYYY-MM-DDTHH:MM".
_MOMENT_FORMAT = "%Y-%m-%dT%H:%M"


def _files_answer(document: Document) -> list[dict[str, object]]:
    """A Document's files (issue 78), oldest first: what each is opened by and shown as.

    Files in the Bin are left out: nobody opens or uses them here (issue 79).
    """
    return [
        {"id": file.public_id, "file_name": file.file_name}
        for file in document.files.all()
        if not file.is_in_bin
    ]


def _in_use(documents: Iterable[Document]) -> list[Document]:
    """The Documents not in the Bin (issue 79), in the order given."""
    return [document for document in documents if not document.is_in_bin]


def _documents_answer(documents: Iterable[Document]) -> list[dict[str, object]]:
    """The Documents linked to an Application or Visa (spec 66), oldest first; none in the Bin."""
    return [
        {
            "id": document.public_id,
            "document_type": {
                "id": document.document_type.public_id,
                "name": document.document_type.name,
            },
            "files": _files_answer(document),
            "verification": document.verification,
        }
        for document in _in_use(documents)
    ]


def _offer_letter(documents: Iterable[Document]) -> dict[str, object] | None:
    """The newest Offer Letter among an Application's Documents (issue 76), or None."""
    letters = [d for d in _in_use(documents) if d.document_type.name.lower() == _OFFER_LETTER_NAME]
    if not letters:
        return None
    newest = letters[-1]
    return {"id": newest.public_id, "files": _files_answer(newest)}


def application_answer(application: Application) -> dict[str, object]:
    """One Application: where to, which Intake ("YYYY-MM", AD), its Status and any offer.

    Read through the Course, so an archived Course or University still shows (spec 50).
    `offered_on` (AD) is null for offers converted from older Statuses (issue 76).
    """
    course = application.course
    university = course.university
    documents = list(application.documents.all())
    offered_on = application.offered_on
    return {
        "id": application.public_id,
        "university": {"id": university.public_id, "name": university.name},
        "course": {"id": course.public_id, "name": course.name, "level": course.level},
        "intake": application.intake,
        "status": application.status,
        "offer_kind": application.offer_kind,
        "offered_on": None if offered_on is None else offered_on.isoformat(),
        "offer_letter": _offer_letter(documents),
        "documents": _documents_answer(documents),
        # The Course's PDF brochures in use, shown under the Application (spec 57).
        "course_files": live_files_answer(course.files.all()),
    }


def visa_answer(visa: Visa) -> dict[str, object]:
    """One Visa: its Application, Status, the day it was applied for (AD) and its appointments."""
    course = visa.application.course
    university = course.university
    return {
        "id": visa.public_id,
        "application_id": visa.application.public_id,
        "university": {"id": university.public_id, "name": university.name},
        "course": {"id": course.public_id, "name": course.name, "level": course.level},
        "status": visa.status,
        "started_on": visa.started_on.isoformat(),
        "biometrics_at": moment_text(visa.biometrics_at),
        "medical_at": moment_text(visa.medical_at),
        "interview_at": moment_text(visa.interview_at),
        "documents": _documents_answer(visa.documents.all()),
    }


def moment_text(moment: datetime | None) -> str | None:
    """A Visa appointment in the Consultancy's time zone ("YYYY-MM-DDTHH:MM"), or None."""
    if moment is None:
        return None
    return timezone.localtime(moment).strftime(_MOMENT_FORMAT)


def appointment_answer(booked: Booked | None) -> dict[str, object] | None:
    """One appointment: which one ("biometrics", "medical" or "interview") and when; or None."""
    if booked is None:
        return None
    return {"kind": booked.kind.value, "at": moment_text(booked.at)}


def _counsellor(counsellor: Staff | None) -> dict[str, object] | None:
    return None if counsellor is None else {"id": counsellor.public_id, "name": counsellor.name}


def office_application_row(application: Application) -> dict[str, object]:
    """One row of the office-wide Applications list (spec 57a).

    The Student's contact details are there to find and reach them; nothing else about them is.
    Days are in the Consultancy's time zone; `applied_on` is null when it was never recorded.
    """
    student = application.student
    counsellor = student.assigned_counsellor
    course = application.course
    university = course.university
    applied_on = application.applied_on
    return {
        "id": application.public_id,
        "student": {
            "id": student.public_id,
            "name": student.name,
            "phone": student.phone,
            "email": student.email,
        },
        "university": {
            "id": university.public_id,
            "name": university.name,
            "country": university.country,
        },
        "course": {"id": course.public_id, "name": course.name, "level": course.level},
        "intake": application.intake,
        "status": application.status,
        "offer_kind": application.offer_kind,
        "assigned_counsellor": _counsellor(counsellor),
        "applied_on": None if applied_on is None else applied_on.isoformat(),
        "added_on": timezone.localdate(application.created_at).isoformat(),
    }


def filter_choices_answer(choices: FilterChoices) -> dict[str, object]:
    """The Intakes, years and countries the list's filters offer."""
    return {"intakes": choices.intakes, "years": choices.years, "countries": choices.countries}


def office_visa_row(visa: Visa, checklist: Checklist) -> dict[str, object]:
    """One row of the office-wide Visas list (spec 62b), with the Student's document count.

    The Student's contact details are there to find and reach them; nothing else about them is.
    """
    student = visa.student
    application = visa.application
    course = application.course
    university = course.university
    counts = checklist.counts
    return {
        "id": visa.public_id,
        "student": {
            "id": student.public_id,
            "name": student.name,
            "phone": student.phone,
            "email": student.email,
        },
        "university": {
            "id": university.public_id,
            "name": university.name,
            "country": university.country,
        },
        "course": {"id": course.public_id, "name": course.name, "level": course.level},
        "intake": application.intake,
        "status": visa.status,
        "next_appointment": appointment_answer(next_appointment(visa.dates, timezone.localdate())),
        "started_on": visa.started_on.isoformat(),
        "documents": {
            "done": checklist.done,
            "total": checklist.total,
            "rejected": counts.rejected,
            "waiting": counts.waiting,
        },
        "assigned_counsellor": _counsellor(student.assigned_counsellor),
    }


def visa_filter_choices_answer(choices: VisaFilterChoices) -> dict[str, object]:
    """The Intakes and countries the Visas list's filters offer."""
    return {"intakes": choices.intakes, "countries": choices.countries}
