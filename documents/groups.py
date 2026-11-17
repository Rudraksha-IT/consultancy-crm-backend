from __future__ import annotations

from django.db import models


class DocumentGroup(models.TextChoices):
    """Which step of the journey a Document is needed for (GLOSSARY: Document Group; issue 80)."""

    APPLYING = "applying", "Applying"
    VISA = "visa", "Visa"
    STATEMENTS = "statements", "Statements"
