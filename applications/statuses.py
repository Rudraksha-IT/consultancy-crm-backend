"""Where an Application or a Visa is (GLOSSARY: Application Status, Visa Status)."""

from __future__ import annotations

from django.db import models


class ApplicationStatus(models.TextChoices):
    """Where an Application currently is (spec 53; issue 74).

    Applied when it is added (or set back by hand), Offer only by adding an Offer Letter, Rejected
    by hand. An offer the Student does not take stays Offer.
    """

    APPLIED = "applied", "Applied"
    OFFER = "offer", "Offer"
    REJECTED = "rejected", "Rejected"


class OfferKind(models.TextChoices):
    """What a University's offer on an Application is (GLOSSARY: Offer Letter)."""

    CONDITIONAL = "conditional", "Conditional"
    UNCONDITIONAL = "unconditional", "Unconditional"


class VisaStatus(models.TextChoices):
    """Where a Visa currently is (spec 59; issue 75).

    A Visa exists only once it is sent to the embassy, so it starts as Applied. Granted ends the
    Student's journey; Refused sends them back. Biometrics, Medical and Interview are dates on the
    Visa, not Statuses.
    """

    APPLIED = "applied", "Applied"
    GRANTED = "granted", "Granted"
    REFUSED = "refused", "Refused"
