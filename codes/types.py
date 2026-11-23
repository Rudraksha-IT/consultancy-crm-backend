from __future__ import annotations

from django.db import models

PURPOSE_MAX_LENGTH = 20


class CodePurpose(models.TextChoices):
    """What a Code is for; a Code only ever works for its own purpose."""

    JOIN = "join", "Join"
    RESET = "reset", "Password reset"
    # A Staff member moving to a new work email; the Code is bound to that email (2026-11-22).
    EMAIL_CHANGE = "email_change", "Email change"
