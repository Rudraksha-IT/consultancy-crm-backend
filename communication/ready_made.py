"""Ready-made emails: which can be used for a Student, and their filled-in drafts (ticket 09).

Spec (2026-11-18) stories 28-46. A draft is filled in only from the Student's own records that the
Staff member may see; a record from another Student or office is never one of the choices. Emails
about one Student's records go to one Student at a time; Plain email and Class details can go to
many, starting "Dear Student,". Fee reminder needs the Accounts Module, Class details Batches.

The starting text itself is written in `emails.drafts`; this module finds what goes in it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from django.db.models import Model
from django.utils import timezone

from access.policy import StudentAction, can_act_on_student, why_cannot_see_payment
from applications.models import Application, Visa
from applications.statuses import ApplicationStatus, OfferKind, VisaStatus
from batches.models import Batch
from catalogue.intakes import intake_label
from clock_in.models import WeeklyOff
from clock_in.services import weekly_off
from communication.attachments import Attachable, suggested_examples
from communication.exceptions import (
    ReadyMadeOffError,
    ReadyMadeRefusedError,
    ReadyMadeUnavailableError,
    RecordNotChosenError,
)
from communication.models import SUBJECT_MAX_LENGTH
from document_examples.services import TypeGuide
from documents.checklist import DocumentState
from documents.services import checklist_of
from emails.drafts import draft_email
from emails.types import (
    CheckingIn,
    ClassDetails,
    DocumentsNeeded,
    Draft,
    DraftContext,
    FeeLine,
    FeeReminder,
    MissingDocument,
    OfferReceived,
    PaymentReceived,
    PlainEmail,
    ReadyMade,
    VisaResult,
    Welcome,
)
from fees.services import balance_of_student, fees_of
from modules.services import is_module_on
from modules.types import Module
from payments.models import Payment, PaymentMethod
from payments.money import format_npr
from payments.services import paid_for, written_date
from staff.models import Staff
from students.models import Stage, Student
from tenancy.models import Consultancy

logger = logging.getLogger(__name__)


class Kind(StrEnum):
    """The nine ready-made emails, in the order the composer offers them."""

    WELCOME = "welcome"
    DOCUMENTS_NEEDED = "documents_needed"
    PAYMENT_RECEIVED = "payment_received"
    FEE_REMINDER = "fee_reminder"
    OFFER_RECEIVED = "offer_received"
    VISA_RESULT = "visa_result"
    CLASS_DETAILS = "class_details"
    CHECKING_IN = "checking_in"
    PLAIN = "plain"


class RecordKind(StrEnum):
    """What a ready-made email is about, picked beside it."""

    PAYMENT = "payment"
    OFFER = "offer"
    VISA = "visa"
    BATCH = "batch"


CHOOSE_MESSAGES = {
    RecordKind.PAYMENT: "Choose a Payment.",
    RecordKind.OFFER: "Choose an Offer Letter.",
    RecordKind.VISA: "Choose a Visa.",
    RecordKind.BATCH: "Choose a class.",
}
NONE_YET_MESSAGES = {
    RecordKind.PAYMENT: "No payments yet",
    RecordKind.OFFER: "No offer letters yet",
    RecordKind.VISA: "No visa result yet",
    RecordKind.BATCH: "Not in a class",
}
NO_OWN_PAYMENT_MESSAGE = "No payment you just recorded"
NO_CLASSES_MESSAGE = "No classes yet"
EVERYTHING_IN_MESSAGE = "Every document is in"
NOTHING_TO_PAY_MESSAGE = "Nothing left to pay"
ONLY_ADMIN_MESSAGE = "Only an Admin can send it"
NO_RECORD_NEEDED_MESSAGE = "This email isn't about a record."
ONE_AT_A_TIME_MESSAGE = "This email goes to one Student at a time."
ONLY_LOST_MESSAGE = "Only for Lost Students"

_MODULE_OF = {Kind.FEE_REMINDER: Module.ACCOUNTS, Kind.CLASS_DETAILS: Module.BATCHES}
_RECORD_OF = {
    Kind.PAYMENT_RECEIVED: RecordKind.PAYMENT,
    Kind.OFFER_RECEIVED: RecordKind.OFFER,
    Kind.VISA_RESULT: RecordKind.VISA,
    Kind.CLASS_DETAILS: RecordKind.BATCH,
}
FOR_MANY = (Kind.CLASS_DETAILS, Kind.CHECKING_IN, Kind.PLAIN)
_STILL_NEEDED = (DocumentState.NOT_RECEIVED, DocumentState.REJECTED)
_OFFICE_DAYS = {
    WeeklyOff.SATURDAY: "Sunday to Friday",
    WeeklyOff.SUNDAY: "Monday to Saturday",
    WeeklyOff.BOTH: "Monday to Friday",
    WeeklyOff.NONE: "Every day",
}


@dataclass(frozen=True)
class Asker:
    """Who asks for the draft (and signs it), in which office."""

    staff: Staff
    consultancy: Consultancy


@dataclass(frozen=True)
class Record:
    """A record a ready-made email can be about, as the picker shows it."""

    id: UUID  # its UUID (ADR 0027)
    label: str


@dataclass(frozen=True)
class DraftWithFiles:
    """A ready-made email's starting text, and the files it suggests attaching (each can go)."""

    draft: Draft
    files: tuple[Attachable, ...] = ()


@dataclass(frozen=True)
class MenuEntry:
    """One ready-made email: what it's about, the records to pick, and why it can't be used."""

    kind: Kind
    record: RecordKind | None
    records: tuple[Record, ...]
    unavailable: str | None


# ---- The records, newest first, only those the Staff member may see ---------------------------


def _payments(student: Student | None, staff: Staff) -> list[Payment]:
    """The Student's Payments: all of them for an Admin, else those just recorded by `staff`."""
    now = timezone.now()
    rows = Payment.objects.filter(student=student).select_related("fee_type")
    return [
        payment
        for payment in rows.order_by("-paid_on", "-pk")
        if why_cannot_see_payment(staff, payment.recorded_by_id, payment.recorded_at, now) is None
    ]


def _offers(student: Student | None, _: Staff) -> list[Application]:
    """The Student's Applications with an Offer Letter."""
    rows = Application.objects.filter(student=student, status=ApplicationStatus.OFFER)
    return list(rows.select_related("course__university").order_by("-pk"))


def _visas(student: Student | None, _: Staff) -> list[Visa]:
    """The Student's Visas with a result (Granted or Refused)."""
    rows = Visa.objects.filter(student=student, status__in=(VisaStatus.GRANTED, VisaStatus.REFUSED))
    return list(rows.select_related("application__course__university").order_by("-pk"))


def _batches(student: Student | None, _: Staff) -> list[Batch]:
    """The classes still running or to come: the Student's own, or every one (`student` None)."""
    rows = Batch.objects.filter(ends_on__gte=timezone.localdate()).select_related("teacher")
    if student is not None:
        rows = rows.filter(memberships__student=student)
    return list(rows.order_by("starts_on", "name", "pk"))


def _payment_label(payment: Payment) -> str:
    amount = format_npr(payment.amount)
    day = written_date(payment.paid_on)
    return f"{paid_for(payment)}, {amount}, {day}"


def _offer_label(application: Application) -> str:
    course = application.course
    kind = OfferKind(application.offer_kind).label if application.offer_kind else "Offer"
    intake = intake_label(application.intake)
    return f"{course.university.name}, {course.name}, {intake} ({kind})"


def _visa_label(visa: Visa) -> str:
    country = visa.application.course.university.get_country_display()
    return f"{country} visa: {VisaStatus(visa.status).label}"


def _batch_label(batch: Batch) -> str:
    return f"{batch.name}, {batch.time_slot}"


def _payment_email(payment: Payment) -> ReadyMade:
    return PaymentReceived(
        amount=payment.amount,
        fee_type=payment.fee_type.name,
        note=payment.note or None,
        paid_on=written_date(payment.paid_on),
        method=PaymentMethod(payment.method).label,
    )


def _offer_email(application: Application) -> ReadyMade:
    course = application.course
    return OfferReceived(
        university=course.university.name,
        course=course.name,
        intake=intake_label(application.intake),
        conditional=application.offer_kind != OfferKind.UNCONDITIONAL,
    )


def _visa_email(visa: Visa) -> ReadyMade:
    return VisaResult(
        country=visa.application.course.university.get_country_display(),
        granted=visa.status == VisaStatus.GRANTED,
    )


def _batch_email(batch: Batch) -> ReadyMade:
    return ClassDetails(
        name=batch.name,
        days=_OFFICE_DAYS[weekly_off()],
        time=batch.time_slot,
        starts=written_date(batch.starts_on),
        teacher=str(batch.teacher.name),
    )


@dataclass(frozen=True)
class _Source:
    """Where one kind of record comes from, how the picker names it, and what the email says."""

    rows: Callable[[Student | None, Staff], Sequence[Any]]
    label: Callable[[Any], str]
    email: Callable[[Any], ReadyMade]


_SOURCES = {
    RecordKind.PAYMENT: _Source(_payments, _payment_label, _payment_email),
    RecordKind.OFFER: _Source(_offers, _offer_label, _offer_email),
    RecordKind.VISA: _Source(_visas, _visa_label, _visa_email),
    RecordKind.BATCH: _Source(_batches, _batch_label, _batch_email),
}


# ---- The emails about the Student alone -------------------------------------------------------


def _welcome(student: Student, _: Staff) -> ReadyMade:
    counsellor = student.assigned_counsellor
    return Welcome(counsellor_name=None if counsellor is None else str(counsellor.name))


def _documents_needed(student: Student, _: Staff) -> ReadyMade:
    email, __ = _documents_needed_with_examples(student)
    return email


def _documents_needed_with_examples(
    student: Student,
) -> tuple[DocumentsNeeded, tuple[Attachable, ...]]:
    """The required Documents not in yet, or Rejected, in checklist order, and their examples.

    Each carries its Document Example instruction; the examples are suggested as attachments,
    and the email says so only when there are some (spec 34).

    Raises:
        ReadyMadeUnavailableError: every required Document is in.
    """
    found = checklist_of(student)
    items = [
        item for item in found.checklist.items if item.is_required and item.state in _STILL_NEEDED
    ]
    if not items:
        raise ReadyMadeUnavailableError(EVERYTHING_IN_MESSAGE)
    guides = [found.guides.get(item.type_id) for item in items]
    missing = tuple(
        MissingDocument(name=item.name, instruction=_instruction(guide))
        for item, guide in zip(items, guides, strict=True)
    )
    files = tuple(
        suggested_examples(
            [guide for guide in guides if guide is not None],
            [item.name for item, guide in zip(items, guides, strict=True) if guide is not None],
        )
    )
    return DocumentsNeeded(documents=missing, has_examples=bool(files)), files


def _instruction(guide: TypeGuide | None) -> str | None:
    return None if guide is None or not guide.instruction else guide.instruction


def _fee_reminder(student: Student, staff: Staff) -> ReadyMade:
    """The Student's Fees, what they paid and the one Balance: for Admins only (spec 36, 76b)."""
    if not can_act_on_student(staff, StudentAction.SEE_BALANCES):
        raise ReadyMadeRefusedError(ONLY_ADMIN_MESSAGE)
    balance = balance_of_student(student)
    if balance.still_to_pay == 0:
        raise ReadyMadeUnavailableError(NOTHING_TO_PAY_MESSAGE)
    fees = tuple(
        FeeLine(fee_type=fee.fee_type.name, note=fee.note or None, amount=fee.amount)
        for fee in fees_of(student)
    )
    return FeeReminder(
        fees=fees, total=balance.fees, paid=balance.paid, still_to_pay=balance.still_to_pay
    )


def _checking_in(student: Student, _: Staff) -> ReadyMade:
    """Asks a Lost Student whether to carry on (list tidy-ups 34); for nobody else.

    Raises:
        ReadyMadeUnavailableError: the Student isn't Lost.
    """
    if student.stage != Stage.LOST:
        raise ReadyMadeUnavailableError(ONLY_LOST_MESSAGE)
    return CheckingIn()


def _plain(_: Student, __: Staff) -> ReadyMade:
    return PlainEmail()


_ABOUT_THE_STUDENT: dict[Kind, Callable[[Student, Staff], ReadyMade]] = {
    Kind.WELCOME: _welcome,
    Kind.DOCUMENTS_NEEDED: _documents_needed,
    Kind.FEE_REMINDER: _fee_reminder,
    Kind.CHECKING_IN: _checking_in,
    Kind.PLAIN: _plain,
}


# ---- The menus ---------------------------------------------------------------------------------


def student_menu(student: Student, asker: Asker) -> list[MenuEntry]:
    """Every ready-made email offered for this Student, with what it needs and why it can't be."""
    return [_student_entry(kind, student, asker.staff) for kind in _kinds_on(asker, list(Kind))]


def many_menu(asker: Asker) -> list[MenuEntry]:
    """The ready-made emails that can go to many Students: Class details, Checking in, Plain."""
    entries = []
    for kind in _kinds_on(asker, list(FOR_MANY)):
        if kind == Kind.CLASS_DETAILS:
            records = _records(RecordKind.BATCH, None, asker.staff)
            reason = None if records else NO_CLASSES_MESSAGE
            entries.append(MenuEntry(kind, RecordKind.BATCH, records, reason))
        else:
            entries.append(MenuEntry(kind, None, (), None))
    return entries


def _kinds_on(asker: Asker, kinds: Iterable[Kind]) -> list[Kind]:
    """`kinds` without those of a switched-off Module."""
    return [
        kind
        for kind in kinds
        if kind not in _MODULE_OF or is_module_on(asker.consultancy, _MODULE_OF[kind])
    ]


def _student_entry(kind: Kind, student: Student, staff: Staff) -> MenuEntry:
    record_kind = _RECORD_OF.get(kind)
    if record_kind is not None:
        records = _records(record_kind, student, staff)
        reason = None if records else _none_message(record_kind, staff)
        return MenuEntry(kind, record_kind, records, reason)
    try:
        _ABOUT_THE_STUDENT[kind](student, staff)
    except (ReadyMadeRefusedError, ReadyMadeUnavailableError) as why:
        return MenuEntry(kind, None, (), why.reason)
    return MenuEntry(kind, None, (), None)


def _records(record_kind: RecordKind, student: Student | None, staff: Staff) -> tuple[Record, ...]:
    source = _SOURCES[record_kind]
    return tuple(Record(_outside_id(row), source.label(row)) for row in source.rows(student, staff))


def _none_message(record_kind: RecordKind, staff: Staff) -> str:
    """Why there is nothing to pick: others than Admins see only Payments they just recorded."""
    sees_every_payment = can_act_on_student(staff, StudentAction.MANAGE_PAYMENTS)
    if record_kind == RecordKind.PAYMENT and not sees_every_payment:
        return NO_OWN_PAYMENT_MESSAGE
    return NONE_YET_MESSAGES[record_kind]


# ---- The drafts --------------------------------------------------------------------------------


def student_draft(student: Student, kind: Kind, record: str | None, asker: Asker) -> DraftWithFiles:
    """The ready-made email `kind` for this Student, about `record` (an id as typed), filled in.

    Raises:
        ReadyMadeOffError: it belongs to a switched-off Module.
        ReadyMadeRefusedError: this Staff member may not send it.
        ReadyMadeUnavailableError: it can't be used for this Student now.
        RecordNotChosenError: the record is missing, not one to pick, or not needed.
    """
    _check_module_on(kind, asker)
    record_kind = _RECORD_OF.get(kind)
    files: tuple[Attachable, ...] = ()
    email: ReadyMade
    if kind == Kind.DOCUMENTS_NEEDED:
        _check_no_record(record)
        email, files = _documents_needed_with_examples(student)
    elif record_kind is None:
        _check_no_record(record)
        email = _ABOUT_THE_STUDENT[kind](student, asker.staff)
    else:
        email = _about(record_kind, student, record, asker.staff)
    logger.info(
        "Email draft made: kind=%s, student_id=%s, attachments=%s",
        kind.value,
        student.pk,
        len(files),
    )
    draft = draft_email(_context(asker, first_name=_first_name(student)), email)
    return DraftWithFiles(_fitting(draft), files)


def many_draft(kind: Kind, record: str | None, asker: Asker) -> DraftWithFiles:
    """The ready-made email `kind` for many Students, starting "Dear Student,".

    Raises:
        ReadyMadeOffError: it belongs to a switched-off Module.
        ReadyMadeUnavailableError: it is about one Student's records.
        RecordNotChosenError: the class is missing or unknown, or a record isn't needed.
    """
    _check_module_on(kind, asker)
    if kind not in FOR_MANY:
        raise ReadyMadeUnavailableError(ONE_AT_A_TIME_MESSAGE)
    if kind == Kind.CLASS_DETAILS:
        email = _about(RecordKind.BATCH, None, record, asker.staff)
    else:
        _check_no_record(record)
        email = CheckingIn() if kind == Kind.CHECKING_IN else PlainEmail()
    logger.info("Email draft made for many: kind=%s, staff_id=%s", kind.value, asker.staff.pk)
    return DraftWithFiles(_fitting(draft_email(_context(asker, first_name=None), email)))


def _check_module_on(kind: Kind, asker: Asker) -> None:
    module = _MODULE_OF.get(kind)
    if module is not None and not is_module_on(asker.consultancy, module):
        raise ReadyMadeOffError


def _check_no_record(record: str | None) -> None:
    if record is not None:
        raise RecordNotChosenError(NO_RECORD_NEEDED_MESSAGE)


def _about(
    record_kind: RecordKind, student: Student | None, record: str | None, staff: Staff
) -> ReadyMade:
    """The email about the record with id `record`, if it is one of those to pick."""
    source = _SOURCES[record_kind]
    for row in source.rows(student, staff):
        if record is not None and str(_outside_id(row)) == record:
            return source.email(row)
    raise RecordNotChosenError(CHOOSE_MESSAGES[record_kind])


def _outside_id(row: Model) -> UUID:
    """The id the picker shows for a record: its UUID (ADR 0027)."""
    public_id: UUID = row.serializable_value("public_id")
    return public_id


def _context(asker: Asker, *, first_name: str | None) -> DraftContext:
    return DraftContext(
        first_name=first_name,
        sender_name=str(asker.staff.name),
        office_name=asker.consultancy.name,
    )


def _first_name(student: Student) -> str:
    words = student.name.split()
    return words[0] if words else student.name


def _fitting(draft: Draft) -> Draft:
    """The draft with its subject line cut to what can be sent (a long name could overflow it)."""
    return Draft(subject=draft.subject[:SUBJECT_MAX_LENGTH], text=draft.text)
