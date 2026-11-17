"""Which Stage moves Staff may make by hand (spec 38-41, 112). Pure: no database, no request.

Lead, Counselling and Lost are set by Staff; Applying, Offer and Visa follow from a Student's
Applications, Offer Letters and Visas (the Stage engine, issues 14, 15 and 74).
"""

from __future__ import annotations

from students.models import Stage

MANUAL_STAGES = frozenset({Stage.LEAD, Stage.COUNSELLING})
AUTOMATIC_STAGES = frozenset({Stage.APPLYING, Stage.OFFER, Stage.VISA})

AUTOMATIC_STAGE_MESSAGE = (
    "Applying, Offer and Visa follow from Applications, offer letters and visas. "
    "They can't be set by hand."
)
ALREADY_AT_STAGE_MESSAGE = "The Student is already at this Stage."
LOST_GOES_BACK_TO_COUNSELLING_MESSAGE = "A Lost Student can only come back to Counselling."
STAGE_FOLLOWS_APPLICATIONS_MESSAGE = (
    "This Student's Stage now follows their Applications, offer letters and visas. "
    "Only Lost can be set by hand."
)


def manual_move_refusal(current: Stage, target: Stage) -> str | None:
    """Why a Student at `current` can't be moved to `target` by hand; None when they can."""
    if target in AUTOMATIC_STAGES:
        return AUTOMATIC_STAGE_MESSAGE
    if target == current:
        return ALREADY_AT_STAGE_MESSAGE
    if current == Stage.LOST:
        return None if target == Stage.COUNSELLING else LOST_GOES_BACK_TO_COUNSELLING_MESSAGE
    if target == Stage.LOST or current in MANUAL_STAGES:
        return None
    return STAGE_FOLLOWS_APPLICATIONS_MESSAGE
