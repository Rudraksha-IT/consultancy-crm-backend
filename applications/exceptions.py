from __future__ import annotations


class TooManyApplicationsError(Exception):
    """The Student already has the most Applications allowed."""


class AlreadyAppliedError(Exception):
    """The Student already has an Application for this Course and Intake."""


class SameStatusError(Exception):
    """The Application already has the Status asked for."""


class TooManyVisasError(Exception):
    """The Student already has the most Visas allowed."""


class VisaWaitingError(Exception):
    """The Student already has a Visa at Applied, waiting for an answer."""


class NoOfferError(Exception):
    """The Application is not the Student's, or has no offer."""


class OfferStaysError(Exception):
    """The Application has an offer, which stays Offer; it is not changed by hand (issue 74)."""


class OfferNeedsLetterError(Exception):
    """Offer is set only by adding an Offer Letter, never by hand (issue 74)."""


class VisaGrantedError(Exception):
    """The Application already has a granted Visa, so no new attempt is needed."""


class VisaDecidedError(Exception):
    """The Visa is Granted or Refused, which is final; a new try is a new Visa."""


class VisaNotWaitingError(Exception):
    """The Visa is Granted or Refused, so its appointment dates no longer change."""


class NotWaitingForOfferError(Exception):
    """An Offer Letter is added only to an Application at Applied (issue 76)."""


class AlreadyChosenError(Exception):
    """This offer is already the Student's Chosen Offer."""


class ChoiceLockedError(Exception):
    """The Chosen Offer's Visa is waiting for an answer, so the choice can't change (issue 76)."""


class NotChosenOfferError(Exception):
    """A Visa is applied for only on the Chosen Offer (issue 76)."""
