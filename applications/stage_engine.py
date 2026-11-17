"""The Stage engine (spec, Implementation Decisions; issues 74, 75). Pure: no database.

Called whenever an Application is added or changes Status (an Offer Letter makes it Offer), or a
Visa is applied for (added at Applied) or changes Status, with the Student's Stage, the Statuses
of all their Applications and Visas (the change already applied) and what changed. Lead,
Counselling and Lost are only ever set by hand, and a Lost Student is never moved (spec open edge
case: a Counsellor brings them back to Counselling first). Rejecting every Application leaves the
Stage alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from applications.statuses import ApplicationStatus, VisaStatus
from students.models import Stage

# The journey in order; Lost is outside it.
JOURNEY = (Stage.LEAD, Stage.COUNSELLING, Stage.APPLYING, Stage.OFFER, Stage.VISA)
# A Visa that keeps the Student at Visa: waiting for an answer, or granted.
_KEEPS_AT_VISA = frozenset({VisaStatus.APPLIED, VisaStatus.GRANTED})


@dataclass(frozen=True)
class ApplicationAdded:
    """The Student has a new Application (always Applied)."""


@dataclass(frozen=True)
class ApplicationChanged:
    """One Application's Status changed to `status`."""

    status: ApplicationStatus


@dataclass(frozen=True)
class VisaChanged:
    """One Visa's Status changed to `status`."""

    status: VisaStatus


StatusChange = ApplicationAdded | ApplicationChanged | VisaChanged


def stage_after(
    current: Stage,
    applications: Sequence[ApplicationStatus],
    visas: Sequence[VisaStatus],
    changed: StatusChange,
) -> Stage:
    """The Student's Stage after `changed`; `current` when it doesn't move them."""
    if current == Stage.LOST:
        return current
    if isinstance(changed, ApplicationAdded):
        return _at_least(current, Stage.APPLYING)
    if isinstance(changed, ApplicationChanged):
        return (
            _at_least(current, Stage.OFFER)
            if changed.status == ApplicationStatus.OFFER
            else current
        )
    return _after_visa(current, applications, visas, changed.status)


def _after_visa(
    current: Stage,
    applications: Sequence[ApplicationStatus],
    visas: Sequence[VisaStatus],
    status: VisaStatus,
) -> Stage:
    if status in _KEEPS_AT_VISA:
        return _at_least(current, Stage.VISA)
    # Refused: back from Visa, unless another Visa still keeps them there.
    if current != Stage.VISA or any(visa in _KEEPS_AT_VISA for visa in visas):
        return current
    return Stage.OFFER if ApplicationStatus.OFFER in applications else Stage.COUNSELLING


def _at_least(current: Stage, stage: Stage) -> Stage:
    return stage if JOURNEY.index(current) < JOURNEY.index(stage) else current
