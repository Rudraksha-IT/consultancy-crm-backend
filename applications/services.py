"""Public services for a Student's Applications and Visas (spec 52-62).

Call inside the Consultancy's schema. Who may do these is the views' job, through
`access.policy`. A Status change locks the Student, asks the Stage engine where they are now, and
writes any Stage move to History in the same transaction.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from applications.appointments import Appointment
from applications.exceptions import (
    AlreadyAppliedError,
    AlreadyChosenError,
    ChoiceLockedError,
    NoOfferError,
    NotChosenOfferError,
    NotWaitingForOfferError,
    OfferNeedsLetterError,
    OfferStaysError,
    SameStatusError,
    TooManyApplicationsError,
    TooManyVisasError,
    VisaDecidedError,
    VisaGrantedError,
    VisaNotWaitingError,
    VisaWaitingError,
)
from applications.models import Application, Visa
from applications.stage_engine import (
    ApplicationAdded,
    ApplicationChanged,
    StatusChange,
    VisaChanged,
    stage_after,
)
from applications.statuses import ApplicationStatus, OfferKind, VisaStatus
from applications.types import DatesChange, NewApplication
from history.models import HistoryKind
from history.services import Change, record
from staff.actors import stored_staff
from staff.models import Staff
from students.models import Stage, Student

# How many Applications one Student can have, closed ones included.
MAX_APPLICATIONS_PER_STUDENT = 30
# How many Visa attempts one Student can have, closed ones included.
MAX_VISAS_PER_STUDENT = 20
AUTOMATIC_DETAIL = "Automatic: {university} application is {status}."
AUTOMATIC_ADDED_DETAIL = "Automatic: {university} application added."
AUTOMATIC_OFFER_DETAIL = "Automatic: offer letter from {university} added."
# The Statuses Staff set by hand; Offer comes only from an Offer Letter (spec 53).
STATUSES_BY_HAND = frozenset({ApplicationStatus.APPLIED, ApplicationStatus.REJECTED})
AUTOMATIC_VISA_DETAIL = "Automatic: {university} visa is {status}."
AUTOMATIC_VISA_APPLIED_DETAIL = "Automatic: {university} visa applied."
OFFER_LETTER_AFTER = "{university}, {kind}"

logger = logging.getLogger(__name__)


def applications_of(student: Student) -> QuerySet[Application]:
    """The Student's Applications, oldest first, with their Course and University."""
    return Application.objects.filter(student=student).select_related("course__university")


def add_application(student: Student, details: NewApplication, by: Staff) -> Application:
    """Add an Application as Applied, recording today as the day; the Stage follows (spec 54).

    Raises:
        TooManyApplicationsError: The Student already has the most allowed.
        AlreadyAppliedError: They already have one for this Course and Intake.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        existing = Application.objects.filter(student=locked)
        if existing.count() >= MAX_APPLICATIONS_PER_STUDENT:
            raise TooManyApplicationsError
        if existing.filter(course=details.course, intake=details.intake).exists():
            raise AlreadyAppliedError
        try:
            with transaction.atomic():
                application = Application.objects.create(
                    student=locked,
                    course=details.course,
                    intake=details.intake,
                    applied_on=timezone.localdate(),
                    created_by=stored_staff(by),
                )
        except IntegrityError as error:  # pragma: no cover  # the lock above prevents it
            raise AlreadyAppliedError from error
        detail = AUTOMATIC_ADDED_DETAIL.format(university=details.course.university.name)
        _follow_with_stage(locked, ApplicationAdded(), detail, by)
    student.stage = locked.stage
    logger.info(
        "Application added: application_id=%s, student_id=%s, by_staff_id=%s",
        application.pk,
        student.pk,
        by.pk,
    )
    return application


def change_application_status(
    student: Student, application_id: int, new_status: ApplicationStatus, by: Staff
) -> None:
    """Set the Application to Applied or Rejected by hand (spec 53); History records it (issue 83).

    Raises:
        Application.DoesNotExist: The Student has no such Application.
        OfferNeedsLetterError: Offer was asked for; only an Offer Letter sets it.
        OfferStaysError: The Application has an offer, which stays Offer.
        SameStatusError: It already has this Status.
    """
    if new_status not in STATUSES_BY_HAND:
        raise OfferNeedsLetterError
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        application = applications_of(locked).select_for_update(of=("self",)).get(pk=application_id)
        before = ApplicationStatus(application.status)
        if before == ApplicationStatus.OFFER:
            raise OfferStaysError
        if before == new_status:
            raise SameStatusError
        university = application.course.university.name
        change = Change(before.value, new_status.value, university)
        record(locked, HistoryKind.APPLICATION_STATUS_CHANGED, by, change)
        _set_status(locked, application, new_status, by)
    student.stage = locked.stage
    _log_status_change(application, before, by)


def record_offer(student: Student, application_id: int, kind: OfferKind, by: Staff) -> None:
    """Make the Application Offer, as adding its Offer Letter does; the Stage follows (spec 55).

    Records the Offer Letter in History. `documents.offer_letters` calls this in the same
    transaction as storing the letter (issue 76).

    Raises:
        Application.DoesNotExist: The Student has no such Application.
        NotWaitingForOfferError: It is not at Applied (it has an offer, or was rejected).
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        application = applications_of(locked).select_for_update(of=("self",)).get(pk=application_id)
        before = ApplicationStatus(application.status)
        if before != ApplicationStatus.APPLIED:
            raise NotWaitingForOfferError
        application.offer_kind = kind
        application.offered_on = timezone.localdate()
        after = OFFER_LETTER_AFTER.format(
            university=application.course.university.name, kind=kind.label
        )
        record(locked, HistoryKind.OFFER_LETTER_ADDED, by, Change(after=after))
        _set_status(locked, application, ApplicationStatus.OFFER, by)
    student.stage = locked.stage
    _log_status_change(application, before, by)


def choose_offer(student: Student, application_id: int, by: Staff) -> None:
    """Make one of the Student's offers their Chosen Offer, replacing any other (spec 115).

    Raises:
        NoOfferError: The Student has no such Application at Offer.
        AlreadyChosenError: It is already the Chosen Offer.
        ChoiceLockedError: The Chosen Offer's Visa is waiting for an answer.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        application = (
            applications_of(locked)
            .filter(pk=application_id, status=ApplicationStatus.OFFER)
            .first()
        )
        if application is None:
            raise NoOfferError
        before = locked.chosen_offer_id
        if before == application.pk:
            raise AlreadyChosenError
        if before is not None and _has_visa_waiting(before):
            raise ChoiceLockedError
        _set_chosen_offer(locked, application, by)
    student.chosen_offer_id = application.pk
    logger.info(
        "Chosen offer changed: application_id=%s, previous_application_id=%s, student_id=%s, "
        "by_staff_id=%s",
        application.pk,
        before,
        student.pk,
        by.pk,
    )


def _set_chosen_offer(
    student: Student, application: Application | None, by: Staff, detail: str = ""
) -> None:
    """Store the (locked) Student's Chosen Offer, or none, and record it in History."""
    before = student.chosen_offer
    student.chosen_offer = application
    student.save(update_fields=["chosen_offer"])
    change = Change(_university_of(before), _university_of(application), detail)
    record(student, HistoryKind.CHOSEN_OFFER_CHANGED, by, change)


def _has_visa_waiting(application_id: int) -> bool:
    return Visa.objects.filter(application_id=application_id, status=VisaStatus.APPLIED).exists()


def _university_of(application: Application | None) -> str:
    return "" if application is None else application.course.university.name


def _set_status(
    student: Student, application: Application, new_status: ApplicationStatus, by: Staff
) -> None:
    """Save the (locked) Application at `new_status` and let the (locked) Student's Stage follow."""
    application.status = new_status
    changed = ["status", "offer_kind"]
    if new_status != ApplicationStatus.OFFER:
        application.offer_kind = ""
    if new_status == ApplicationStatus.OFFER:
        changed.append("offered_on")
    if new_status == ApplicationStatus.APPLIED and application.applied_on is None:
        application.applied_on = timezone.localdate()
        changed.append("applied_on")
    application.save(update_fields=changed)
    university = application.course.university.name
    detail = (
        AUTOMATIC_OFFER_DETAIL.format(university=university)
        if new_status == ApplicationStatus.OFFER
        else AUTOMATIC_DETAIL.format(university=university, status=new_status.label)
    )
    _follow_with_stage(student, ApplicationChanged(new_status), detail, by)


def _log_status_change(application: Application, before: ApplicationStatus, by: Staff) -> None:
    logger.info(
        "Application status changed: application_id=%s, from=%s, to=%s, by_staff_id=%s",
        application.pk,
        before.value,
        application.status,
        by.pk,
    )


def visas_of(student: Student) -> QuerySet[Visa]:
    """The Student's Visas, newest first, with their Application's Course and University."""
    return Visa.objects.filter(student=student).select_related("application__course__university")


def start_visa(student: Student, application_id: int, by: Staff) -> Visa:
    """Add a Visa as Applied, once it is sent to the embassy, for the Student's Chosen Offer.

    The Student moves to Visa (spec 58; issues 75 and 76).

    Raises:
        NotChosenOfferError: The Application is not the Student's Chosen Offer.
        VisaWaitingError: The Student already has a Visa waiting for an answer.
        VisaGrantedError: That Application already has a granted Visa.
        TooManyVisasError: The Student already has the most allowed.
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        application = (
            applications_of(locked)
            .filter(pk=application_id, status=ApplicationStatus.OFFER)
            .first()
        )
        if application is None or application.pk != locked.chosen_offer_id:
            raise NotChosenOfferError
        existing = Visa.objects.filter(student=locked)
        if existing.count() >= MAX_VISAS_PER_STUDENT:
            raise TooManyVisasError
        if existing.filter(status=VisaStatus.APPLIED).exists():
            raise VisaWaitingError
        if existing.filter(application=application, status=VisaStatus.GRANTED).exists():
            raise VisaGrantedError
        visa = Visa.objects.create(
            student=locked,
            application=application,
            status=VisaStatus.APPLIED,
            created_by=stored_staff(by),
        )
        detail = AUTOMATIC_VISA_APPLIED_DETAIL.format(university=application.course.university.name)
        _follow_with_stage(locked, VisaChanged(VisaStatus.APPLIED), detail, by)
    student.stage = locked.stage
    logger.info(
        "Visa applied: visa_id=%s, application_id=%s, student_id=%s, by_staff_id=%s",
        visa.pk,
        application.pk,
        student.pk,
        by.pk,
    )
    return visa


def change_visa_status(student: Student, visa_id: int, new_status: VisaStatus, by: Staff) -> None:
    """Decide a Visa waiting for an answer by hand; the Stage moves as the engine says (spec 59-61).

    Granted and Refused are final (UI revamp ticket 10): a new try is a new Visa.
    The Status change is recorded in History before any Stage move (issue 83).

    Raises:
        Visa.DoesNotExist: The Student has no such Visa.
        VisaDecidedError: It is Granted or Refused already; nothing is changed.
        SameStatusError: It already has this Status.

    Refused clears the Chosen Offer if it is this Visa's, so the Student may choose again;
    Granted makes it the Chosen Offer when there is none (issue 76).
    """
    with transaction.atomic():
        locked = Student.objects.select_for_update().get(pk=student.pk)
        visa = visas_of(locked).select_for_update(of=("self",)).get(pk=visa_id)
        before = VisaStatus(visa.status)
        if before != VisaStatus.APPLIED:
            logger.info(
                "Visa status not changed: visa_id=%s, refused=decided, by_staff_id=%s",
                visa.pk,
                by.pk,
            )
            raise VisaDecidedError
        if before == new_status:
            raise SameStatusError
        visa.status = new_status
        changed = ["status"]
        if new_status == VisaStatus.GRANTED:
            visa.granted_on = timezone.localdate()
            changed.append("granted_on")
        visa.save(update_fields=changed)
        university = visa.application.course.university.name
        change = Change(before.value, new_status.value, university)
        record(locked, HistoryKind.VISA_STATUS_CHANGED, by, change)
        detail = AUTOMATIC_VISA_DETAIL.format(university=university, status=new_status.label)
        _follow_chosen_offer(locked, visa, detail, by)
        _follow_with_stage(locked, VisaChanged(new_status), detail, by)
    student.stage = locked.stage
    logger.info(
        "Visa status changed: visa_id=%s, from=%s, to=%s, by_staff_id=%s",
        visa.pk,
        before.value,
        new_status.value,
        by.pk,
    )


def change_visa_dates(student: Student, visa_id: int, change: DatesChange, by: Staff) -> None:
    """Book, move or clear (None) a Visa's Biometrics, Medical or Interview (spec 62a).

    Only the appointments in `change` are touched. Only a Visa waiting for an answer has dates.
    Each appointment that really changes adds a Visa date entry to the Student's History
    (issue 83).

    Raises:
        Visa.DoesNotExist: The Student has no such Visa.
        VisaNotWaitingError: The Visa is Granted or Refused.
    """
    with transaction.atomic():
        visa = Visa.objects.select_for_update().get(student=student, pk=visa_id)
        if visa.status != VisaStatus.APPLIED:
            raise VisaNotWaitingError
        fields = [_DATE_FIELDS[kind] for kind in change]
        for kind, moment in change.items():
            was: datetime | None = getattr(visa, _DATE_FIELDS[kind])
            if was != moment:
                dates = Change(_moment_text(was), _moment_text(moment), kind.value)
                record(student, HistoryKind.VISA_DATE_CHANGED, by, dates)
            setattr(visa, _DATE_FIELDS[kind], moment)
        visa.save(update_fields=fields)
    logger.info(
        "Visa dates changed: visa_id=%s, student_id=%s, changed=%s, cleared=%s, by_staff_id=%s",
        visa.pk,
        student.pk,
        ",".join(kind.value for kind in change),
        ",".join(kind.value for kind, moment in change.items() if moment is None),
        by.pk,
    )


def _moment_text(moment: datetime | None) -> str:
    """A Visa date as History keeps it: UTC ISO 8601, or empty for none."""
    return "" if moment is None else moment.astimezone(UTC).isoformat()


def _follow_chosen_offer(student: Student, visa: Visa, detail: str, by: Staff) -> None:
    """Keep the (locked) Student's Chosen Offer in step with the Visa's new Status.

    Refused clears it when it is this Visa's offer; Applied or Granted chooses this Visa's offer
    when there is none.
    """
    chosen = student.chosen_offer_id
    if visa.status == VisaStatus.REFUSED and chosen == visa.application_id:
        _set_chosen_offer(student, None, by, detail)
    elif visa.status != VisaStatus.REFUSED and chosen is None:
        _set_chosen_offer(student, visa.application, by, detail)


# Where each appointment is kept on the Visa.
_DATE_FIELDS = {
    Appointment.BIOMETRICS: "biometrics_at",
    Appointment.MEDICAL: "medical_at",
    Appointment.INTERVIEW: "interview_at",
}


def _follow_with_stage(student: Student, changed: StatusChange, detail: str, by: Staff) -> None:
    """Move the (locked) Student where the engine says, recording it in History."""
    applications = [
        ApplicationStatus(value)
        for value in Application.objects.filter(student=student).values_list("status", flat=True)
    ]
    visas = [
        VisaStatus(value)
        for value in Visa.objects.filter(student=student).values_list("status", flat=True)
    ]
    before = Stage(student.stage)
    after = stage_after(before, applications, visas, changed)
    if after == before:
        return
    student.stage = after
    student.save(update_fields=["stage"])
    record(student, HistoryKind.STAGE_CHANGED, by, Change(before.value, after.value, detail))
    logger.info(
        "Stage changed automatically: student_id=%s, from=%s, to=%s, because=%s",
        student.pk,
        before.value,
        after.value,
        type(changed).__name__,
    )
