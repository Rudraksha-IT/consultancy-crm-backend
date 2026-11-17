"""How far along a Student is and what comes next (spec 22a, 113). Pure: no database.

One rule for Home and the Student's page, for every Stage (issue 74). The percent places a
Student on the journey from Lead (8) to a granted Visa (100). While Applying, their open
Applications decide the next step; at Offer, the offers received or the Chosen Offer (issue 76);
at Visa, the next appointment of the Visa waiting for an answer (issue 75). A granted Visa is the
end of the journey.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from django.utils import timezone

from applications.appointments import Appointment, VisaDates, upcoming
from applications.statuses import ApplicationStatus, VisaStatus
from students.models import Stage

DONE_PERCENT = 100
LOST_PERCENT = 0


@dataclass(frozen=True)
class ApplicationAt:
    """One of the Student's Applications: its Status, University and whether it is chosen."""

    status: ApplicationStatus
    university: str
    # Whether it is the Student's Chosen Offer (issue 76).
    is_chosen: bool = False


@dataclass(frozen=True)
class VisaAt:
    """One of the Student's Visas: its Status and the appointments booked on it."""

    status: VisaStatus
    dates: VisaDates = field(default_factory=VisaDates)


@dataclass(frozen=True)
class Progress:
    """How far along the Student is (0 to 100), their next step, and whether they are done."""

    percent: int
    next_step: str
    is_done: bool = False


_BY_HAND: dict[Stage, Progress] = {
    Stage.LEAD: Progress(8, "Have the first counselling session"),
    Stage.COUNSELLING: Progress(20, "Choose a course and apply"),
}
_WAITING_FOR_OFFER_PERCENT = 45
_NEW_OPTIONS = Progress(35, "Find new options")
_OFFER_PERCENT = 70
_CHOSEN_OFFER_PERCENT = 76
_VISA_PERCENT = 88
_BOOK_BIOMETRICS = "Book biometrics"
_WAITING_FOR_EMBASSY = "Waiting for the embassy"
_GRANTED = Progress(
    DONE_PERCENT, "Visa granted. Help with flights and pre-departure.", is_done=True
)


def progress_of(
    stage: Stage,
    applications: Sequence[ApplicationAt],
    visas: Sequence[VisaAt],
    lost_reason: str = "",
    today: date | None = None,
) -> Progress:
    """Return how far along a Student at `stage` is, given their Applications and Visas.

    `today` (the Consultancy's day; today by default) decides which appointments are coming up.
    """
    if stage == Stage.LOST:
        return Progress(LOST_PERCENT, f"Lost: {lost_reason}")
    if stage in _BY_HAND:
        return _BY_HAND[stage]
    return _FOLLOWING[stage](applications, visas, today or timezone.localdate())


def _applying(
    applications: Sequence[ApplicationAt], _visas: Sequence[VisaAt], _today: date
) -> Progress:
    waiting = [one.university for one in applications if one.status == ApplicationStatus.APPLIED]
    if not waiting:
        return _NEW_OPTIONS
    return Progress(_WAITING_FOR_OFFER_PERCENT, f"Waiting for an offer, {', '.join(waiting)}")


def _at_offer(
    applications: Sequence[ApplicationAt], _visas: Sequence[VisaAt], _today: date
) -> Progress:
    """Choose one of the offers; once chosen, the visa papers for it are next (issue 76)."""
    offers = [one for one in applications if one.status == ApplicationStatus.OFFER]
    chosen = next((one for one in offers if one.is_chosen), None)
    if chosen is not None:
        return Progress(_CHOSEN_OFFER_PERCENT, f"Prepare the visa papers, {chosen.university}")
    return Progress(_OFFER_PERCENT, f"Choose an offer ({len(offers)} received)")


def _at_visa(
    _applications: Sequence[ApplicationAt], visas: Sequence[VisaAt], today: date
) -> Progress:
    """The next appointment of the Visa waiting for an answer leads; then the embassy's answer."""
    if any(visa.status == VisaStatus.GRANTED for visa in visas):
        return _GRANTED
    dates = next(
        (visa.dates for visa in reversed(visas) if visa.status == VisaStatus.APPLIED), VisaDates()
    )
    soon = upcoming(dates, today)
    if soon is not None:
        return Progress(_VISA_PERCENT, f"{soon.kind.label} coming up")
    booked = {one.kind for one in dates.booked()}
    step = _WAITING_FOR_EMBASSY if Appointment.BIOMETRICS in booked else _BOOK_BIOMETRICS
    return Progress(_VISA_PERCENT, step)


_Rule = Callable[[Sequence[ApplicationAt], Sequence[VisaAt], date], Progress]
# The Stages that follow from Applications, Offer Letters and Visas.
_FOLLOWING: dict[Stage, _Rule] = {
    Stage.APPLYING: _applying,
    Stage.OFFER: _at_offer,
    Stage.VISA: _at_visa,
}
