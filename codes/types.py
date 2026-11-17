from __future__ import annotations

from django.db import models

PURPOSE_MAX_LENGTH = 10


class CodePurpose(models.TextChoices):
    """What a Code is for; a Code only ever works for its own purpose."""

    JOIN = "join", "Join"
    RESET = "reset", "Password reset"
