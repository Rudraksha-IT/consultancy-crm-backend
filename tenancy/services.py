"""Public services for the Consultancy lifecycle."""

from __future__ import annotations

import io
import logging
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from django.db import transaction
from django.utils import timezone

from documents.exceptions import DocumentStorageError
from documents.storage import document_storage
from emails.types import OfficeDetails
from tenancy.exceptions import InvalidStatusChangeError, NoLogoError
from tenancy.logo import CheckedLogo
from tenancy.models import (
    STATUS_REASON_MAX_LENGTH,
    Consultancy,
    ConsultancyStatusChange,
    Domain,
    SetupState,
)
from tenancy.status import (
    EVALUATION_LENGTH,
    REASON_REQUIRED,
    ConsultancyStatus,
    can_move,
    restriction_message,
)
from tenancy.subdomains import build_host, schema_name_for
from tenancy.types import ConsultancyAccess, DateDisplay, OfficeLogo, StatusChange, StoredLogo

if TYPE_CHECKING:
    from platform_admin.models import PlatformAdmin

OFFICE_DETAILS_FIELDS = ("contact_email", "phone", "address_line1", "address_line2", "website")
# Where the frontend loads the logo from; each upload has its own version, so its own address.
LOGO_URL = "/api/consultancy/logo/{version}/"
_LOGO_FIELDS = ["logo_key", "logo_content_type", "logo_version"]
_STATUS_FIELDS = [
    "status",
    "status_reason",
    "evaluation_ends_at",
    "paid_until",
    "suspended_at",
    "terminated_at",
]
# How far ahead an evaluation end or a paid-until date may be set.
LATEST_DATE_AHEAD = timedelta(days=3650)

STATUS_LABELS = {
    ConsultancyStatus.EVALUATION: "Evaluation",
    ConsultancyStatus.ACTIVE: "Active",
    ConsultancyStatus.PAST_DUE: "Past due",
    ConsultancyStatus.SUSPENDED: "Suspended",
    ConsultancyStatus.TERMINATED: "Terminated",
}
TERMINATED_IS_FINAL_MESSAGE = "A Terminated Consultancy can't be changed."
ALREADY_STARTED_MESSAGE = "This Consultancy already has a Status."
NOT_IN_EVALUATION_MESSAGE = "Only a Consultancy in Evaluation can have its evaluation extended."
REASON_NEEDED_MESSAGE = "Give a reason."
REASON_TOO_LONG_MESSAGE = f"Keep the reason to {STATUS_REASON_MAX_LENGTH} characters."
DATE_NOT_IN_FUTURE_MESSAGE = "Choose a date after today."
DATE_NEEDED_MESSAGE = "Choose a date."
DATE_TOO_FAR_MESSAGE = "Choose a date within 10 years."
NOT_READY_MESSAGE = "This office is still being set up, or its setup failed."

logger = logging.getLogger(__name__)


@transaction.atomic
def create_consultancy(name: str, schema_name: str, host: str) -> Consultancy:
    """Create a ready Consultancy with its own schema, reachable at the given host, at once.

    The Platform builds new offices in a background job instead (`reserve_consultancy`); this
    is for tests and scripts that need an office straight away.

    Args:
        name: Display name of the Consultancy.
        schema_name: Name of the PostgreSQL schema that will hold its data.
        host: Full host name (subdomain) that resolves to it.

    Returns:
        The saved Consultancy, with its schema migrated, in Evaluation for 14 days. Record its
        starting Status with `start_evaluation` or `start_active`.
    """
    consultancy = Consultancy(
        name=name,
        schema_name=schema_name,
        status=ConsultancyStatus.EVALUATION.value,
        evaluation_ends_at=timezone.now() + EVALUATION_LENGTH,
        setup_state=SetupState.READY,
    )
    consultancy.save()
    consultancy.create_schema(check_if_exists=True, verbosity=0)
    Domain.objects.create(domain=host, tenant=consultancy, is_primary=True)
    return consultancy


@transaction.atomic
def reserve_consultancy(name: str, schema_name: str, host: str) -> Consultancy:
    """Save a new Consultancy without its schema, its host switched off, while it is set up.

    The name, schema and host are taken from now on. The setup job builds the schema and makes
    it ready (issue 60); until then its host answers 404.

    Returns:
        The saved Consultancy, `setting_up`, in Evaluation until the job records its start.
    """
    consultancy = Consultancy(
        name=name,
        schema_name=schema_name,
        status=ConsultancyStatus.EVALUATION.value,
        setup_state=SetupState.SETTING_UP,
    )
    consultancy.save()
    Domain.objects.create(domain=host, tenant=consultancy, is_primary=True, is_active=False)
    return consultancy


def is_ready(consultancy: Consultancy) -> bool:
    """Whether the Consultancy's setup has finished, so it can be reached and worked on."""
    return str(consultancy.setup_state) == SetupState.READY.value


def check_ready(consultancy: Consultancy) -> None:
    """Refuse to work on a Consultancy whose setup hasn't finished.

    Raises:
        InvalidStatusChangeError: It is still being set up, or its setup failed.
    """
    if not is_ready(consultancy):
        raise InvalidStatusChangeError(NOT_READY_MESSAGE)


def is_subdomain_taken(subdomain: str) -> bool:
    """Return whether any Consultancy, even a Terminated one, uses this host or schema."""
    is_host_taken: bool = Domain.objects.filter(domain=build_host(subdomain)).exists()
    is_schema_taken = Consultancy.objects.filter(schema_name=schema_name_for(subdomain)).exists()
    return is_host_taken or is_schema_taken


def get_status(consultancy: Consultancy) -> ConsultancyStatus:
    """Return the Consultancy's Status; a damaged stored value counts as Suspended (read-only)."""
    try:
        return ConsultancyStatus(consultancy.status)
    except ValueError:
        logger.warning("damaged status read as suspended: consultancy_id=%s", consultancy.pk)
        return ConsultancyStatus.SUSPENDED


def get_access(consultancy: Consultancy, now: datetime | None = None) -> ConsultancyAccess:
    """Return the Status Staff are shown, with why it limits them (None when it doesn't)."""
    status = get_status(consultancy)
    ends_at = consultancy.evaluation_ends_at
    return ConsultancyAccess(
        status=status,
        message=restriction_message(status, ends_at, now or timezone.now()),
        evaluation_ends_at=ends_at if status == ConsultancyStatus.EVALUATION else None,
    )


def change_status(consultancy: Consultancy, change: StatusChange, *, by: PlatformAdmin) -> None:
    """Make a Platform Admin's Status change through the service for that move.

    Raises:
        InvalidStatusChangeError: The move, its date or its reason is refused, the date it
            needs is missing, or the Consultancy isn't set up yet.
    """
    check_ready(consultancy)
    reason = change.reason
    if change.status == ConsultancyStatus.EVALUATION:
        ends_at = _required(change.evaluation_ends_at)
        extend_evaluation(consultancy, ends_at=ends_at, reason=reason, by=by)
    elif change.status == ConsultancyStatus.ACTIVE:
        activate(consultancy, paid_until=_required(change.paid_until), reason=reason, by=by)
    else:
        _REASON_ONLY_MOVES[change.status](consultancy, reason=reason, by=by)


def _required[T](value: T | None) -> T:
    if value is None:
        raise InvalidStatusChangeError(DATE_NEEDED_MESSAGE)
    return value


def start_evaluation(consultancy: Consultancy, *, by: PlatformAdmin) -> None:
    """Record a new Consultancy's start: Evaluation, ending 14 days from now (spec 13a).

    Raises:
        InvalidStatusChangeError: The Consultancy has started already.
    """
    _record_start(
        consultancy,
        by=by,
        status=ConsultancyStatus.EVALUATION,
        evaluation_ends_at=timezone.now() + EVALUATION_LENGTH,
        paid_until=None,
    )


def start_active(consultancy: Consultancy, *, paid_until: date, by: PlatformAdmin) -> None:
    """Record a new Consultancy's start as Active, paid up to `paid_until` (spec 13a).

    Raises:
        InvalidStatusChangeError: The Consultancy has started already, or the date is refused.
    """
    _check_future_date(paid_until)
    _record_start(
        consultancy,
        by=by,
        status=ConsultancyStatus.ACTIVE,
        evaluation_ends_at=None,
        paid_until=paid_until,
    )


def extend_evaluation(
    consultancy: Consultancy, *, ends_at: datetime, reason: str, by: PlatformAdmin
) -> None:
    """Move an evaluation's end to a later moment, keeping the Status (spec 13a).

    Raises:
        InvalidStatusChangeError: Not in Evaluation, the moment isn't in the future, or the
            reason is too long.
    """
    reason = _checked_reason(ConsultancyStatus.EVALUATION, reason)
    _check_future_moment(ends_at)
    with transaction.atomic():
        locked = _lock(consultancy)
        if get_status(locked) != ConsultancyStatus.EVALUATION:
            raise InvalidStatusChangeError(NOT_IN_EVALUATION_MESSAGE)
        locked.evaluation_ends_at = ends_at
        _save_change(locked, ConsultancyStatus.EVALUATION, reason, by)
    _copy_status(locked, consultancy)


def activate(consultancy: Consultancy, *, paid_until: date, reason: str, by: PlatformAdmin) -> None:
    """Make the Consultancy Active, paid up to `paid_until`; everything works again.

    Raises:
        InvalidStatusChangeError: The move or the date is refused.
    """
    _check_future_date(paid_until)

    def apply(locked: Consultancy) -> None:
        locked.paid_until = paid_until
        locked.evaluation_ends_at = None
        locked.suspended_at = None

    _move(consultancy, ConsultancyStatus.ACTIVE, reason, by, apply)


def mark_past_due(consultancy: Consultancy, *, reason: str, by: PlatformAdmin) -> None:
    """Make the Consultancy Past due: Staff can read and log in but change nothing.

    Raises:
        InvalidStatusChangeError: The move is refused or the reason is missing.
    """

    def apply(locked: Consultancy) -> None:
        locked.suspended_at = None

    _move(consultancy, ConsultancyStatus.PAST_DUE, reason, by, apply)


def suspend(consultancy: Consultancy, *, reason: str, by: PlatformAdmin) -> None:
    """Suspend the Consultancy: Staff can read and log in but change nothing.

    Raises:
        InvalidStatusChangeError: The move is refused or the reason is missing.
    """

    def apply(locked: Consultancy) -> None:
        locked.suspended_at = timezone.now()

    _move(consultancy, ConsultancyStatus.SUSPENDED, reason, by, apply)


def terminate(consultancy: Consultancy, *, reason: str, by: PlatformAdmin) -> None:
    """Close the Consultancy for good: every one of its hosts is switched off. Data is kept.

    Raises:
        InvalidStatusChangeError: Already Terminated, or the reason is missing.
    """

    def apply(locked: Consultancy) -> None:
        locked.terminated_at = timezone.now()
        Domain.objects.filter(tenant=locked).update(is_active=False)

    _move(consultancy, ConsultancyStatus.TERMINATED, reason, by, apply)


class _ReasonOnlyMove(Protocol):
    def __call__(self, consultancy: Consultancy, *, reason: str, by: PlatformAdmin) -> None: ...


_REASON_ONLY_MOVES: dict[ConsultancyStatus, _ReasonOnlyMove] = {
    ConsultancyStatus.PAST_DUE: mark_past_due,
    ConsultancyStatus.SUSPENDED: suspend,
    ConsultancyStatus.TERMINATED: terminate,
}


def _move(
    consultancy: Consultancy,
    to_status: ConsultancyStatus,
    reason: str,
    by: PlatformAdmin,
    apply: Callable[[Consultancy], None],
) -> None:
    reason = _checked_reason(to_status, reason)
    with transaction.atomic():
        locked = _lock(consultancy)
        from_status = get_status(locked)
        if not can_move(from_status, to_status):
            raise InvalidStatusChangeError(_refused_move_message(from_status, to_status))
        apply(locked)
        locked.status = to_status.value
        _save_change(locked, from_status, reason, by)
    _copy_status(locked, consultancy)
    logger.info(
        "Consultancy Status changed: consultancy_id=%s, from=%s, to=%s, platform_admin_id=%s",
        consultancy.pk,
        from_status.value,
        to_status.value,
        by.pk,
    )


def _record_start(
    consultancy: Consultancy,
    *,
    by: PlatformAdmin,
    status: ConsultancyStatus,
    evaluation_ends_at: datetime | None,
    paid_until: date | None,
) -> None:
    with transaction.atomic():
        locked = _lock(consultancy)
        if ConsultancyStatusChange.objects.filter(consultancy=locked).exists():
            raise InvalidStatusChangeError(ALREADY_STARTED_MESSAGE)
        locked.status = status.value
        locked.evaluation_ends_at = evaluation_ends_at
        locked.paid_until = paid_until
        _save_change(locked, None, "", by)
    _copy_status(locked, consultancy)


def _save_change(
    locked: Consultancy, from_status: ConsultancyStatus | None, reason: str, by: PlatformAdmin
) -> None:
    locked.status_reason = reason
    locked.save(update_fields=_STATUS_FIELDS)
    ConsultancyStatusChange.objects.create(
        consultancy=locked,
        changed_by=by,
        from_status="" if from_status is None else from_status.value,
        to_status=locked.status,
        reason=reason,
        evaluation_ends_at=locked.evaluation_ends_at,
        paid_until=locked.paid_until,
    )


def _lock(consultancy: Consultancy) -> Consultancy:
    locked: Consultancy = Consultancy.objects.select_for_update().get(pk=consultancy.pk)
    return locked


def _copy_status(source: Consultancy, target: Consultancy) -> None:
    for field in _STATUS_FIELDS:
        setattr(target, field, getattr(source, field))


def _checked_reason(to_status: ConsultancyStatus, reason: str) -> str:
    reason = reason.strip()
    if to_status in REASON_REQUIRED and not reason:
        raise InvalidStatusChangeError(REASON_NEEDED_MESSAGE)
    if len(reason) > STATUS_REASON_MAX_LENGTH:
        raise InvalidStatusChangeError(REASON_TOO_LONG_MESSAGE)
    return reason


def _check_future_moment(moment: datetime) -> None:
    now = timezone.now()
    if moment <= now:
        raise InvalidStatusChangeError(DATE_NOT_IN_FUTURE_MESSAGE)
    if moment > now + LATEST_DATE_AHEAD:
        raise InvalidStatusChangeError(DATE_TOO_FAR_MESSAGE)


def check_paid_until(day: date) -> None:
    """Accept a paid-until date only after today and within 10 years.

    Raises:
        InvalidStatusChangeError: The date is refused.
    """
    _check_future_date(day)


def _check_future_date(day: date) -> None:
    today = timezone.localdate()
    if day <= today:
        raise InvalidStatusChangeError(DATE_NOT_IN_FUTURE_MESSAGE)
    if day > today + LATEST_DATE_AHEAD:
        raise InvalidStatusChangeError(DATE_TOO_FAR_MESSAGE)


def _refused_move_message(from_status: ConsultancyStatus, to_status: ConsultancyStatus) -> str:
    if from_status == ConsultancyStatus.TERMINATED:
        return TERMINATED_IS_FINAL_MESSAGE
    return f"{STATUS_LABELS[from_status]} can't change to {STATUS_LABELS[to_status]}."


def get_date_display(consultancy: Consultancy) -> DateDisplay:
    """Return the calendar the Consultancy shows dates in; a damaged stored value means AD."""
    try:
        return DateDisplay(consultancy.date_display)
    except ValueError:
        logger.warning("damaged date display read as AD: consultancy_id=%s", consultancy.pk)
        return DateDisplay.AD


def set_date_display(consultancy: Consultancy, date_display: DateDisplay) -> None:
    """Show the Consultancy's dates in AD or BS from now on; stored dates stay in AD."""
    consultancy.date_display = date_display.value
    consultancy.save(update_fields=["date_display"])


def get_office_details(consultancy: Consultancy) -> OfficeDetails:
    """Return the office's contact email, phone, address and website (empty when not set)."""
    return OfficeDetails(
        contact_email=consultancy.contact_email,
        phone=consultancy.phone,
        address_line1=consultancy.address_line1,
        address_line2=consultancy.address_line2,
        website=consultancy.website,
    )


def set_office_details(consultancy: Consultancy, details: OfficeDetails) -> OfficeDetails:
    """Store the office's details (already checked) in place of the old ones, and return them."""
    consultancy.contact_email = details.contact_email
    consultancy.phone = details.phone
    consultancy.address_line1 = details.address_line1
    consultancy.address_line2 = details.address_line2
    consultancy.website = details.website
    consultancy.save(update_fields=list(OFFICE_DETAILS_FIELDS))
    return get_office_details(consultancy)


def get_logo(consultancy: Consultancy) -> OfficeLogo | None:
    """Return the office logo the screens and receipts show, or None (the initials show)."""
    if not consultancy.logo_key:
        return None
    return OfficeLogo(url=LOGO_URL.format(version=consultancy.logo_version))


def set_logo(consultancy: Consultancy, logo: CheckedLogo) -> OfficeLogo:
    """Store a checked logo in private storage, under the Consultancy's prefix, and show it.

    The old logo's file is removed afterwards.

    Raises:
        DocumentStorageError: The file couldn't be stored; the old logo stays.
    """
    old_key = consultancy.logo_key
    version = consultancy.logo_version + 1
    key = f"{consultancy.schema_name}/office-logo/{version}-{uuid.uuid4().hex}{logo.kind.extension}"
    document_storage().save(key, io.BytesIO(logo.content), logo.kind.content_type)
    consultancy.logo_key = key
    consultancy.logo_content_type = logo.kind.content_type
    consultancy.logo_version = version
    consultancy.save(update_fields=_LOGO_FIELDS)
    _remove_file(consultancy, old_key)
    shown = get_logo(consultancy)
    if shown is None:  # pragma: no cover  # a key was just stored
        raise NoLogoError
    return shown


def remove_logo(consultancy: Consultancy) -> None:
    """Stop showing the office logo (the initials show again) and remove its file."""
    old_key = consultancy.logo_key
    consultancy.logo_key = ""
    consultancy.logo_content_type = ""
    consultancy.save(update_fields=_LOGO_FIELDS)
    _remove_file(consultancy, old_key)


def read_logo(consultancy: Consultancy, version: int) -> StoredLogo | None:
    """Return the logo's bytes if `version` is the one shown now, otherwise None.

    Raises:
        DocumentStorageError: The file couldn't be read.
    """
    if not consultancy.logo_key or version != consultancy.logo_version:
        return None
    content = document_storage().read(consultancy.logo_key)
    return StoredLogo(content, consultancy.logo_content_type)


def _remove_file(consultancy: Consultancy, key: str) -> None:
    if not key:
        return
    try:
        document_storage().delete(key)
    except DocumentStorageError:
        logger.warning("Old office logo file not removed: consultancy_id=%s", consultancy.pk)
