from __future__ import annotations

from django.db import models


class Verification(models.TextChoices):
    """The judgment on a Document (GLOSSARY: Verification). Every new one starts Pending."""

    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"
