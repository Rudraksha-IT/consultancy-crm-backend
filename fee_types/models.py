"""Fee Types: what a Fee or Payment is for, from the office's own list (GLOSSARY: Fee Type).

An Admin keeps the list in Configuration. "Other" is always there, can't be renamed, hidden or
deleted, and asks for a short note. A Fee Type in use is never deleted, only hidden.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

FEE_TYPE_NAME_MAX_LENGTH = 60
# A Fee's or Payment's note: long enough to keep every purpose typed before Fee Types.
NOTE_MAX_LENGTH = 200


class FeeType(models.Model):
    """One Fee Type: its name, its place in every list, and whether it is still offered."""

    name = models.CharField(max_length=FEE_TYPE_NAME_MAX_LENGTH)
    position = models.PositiveIntegerField()
    hidden = models.BooleanField(default=False)
    # The one "Other" Fee Type, which needs a note on every Fee and Payment.
    is_other = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "pk")
        constraints = (
            models.UniqueConstraint(Lower("name"), name="fee_types_unique_name"),
            models.UniqueConstraint(
                fields=("is_other",), condition=Q(is_other=True), name="fee_types_one_other"
            ),
            models.CheckConstraint(
                condition=Q(is_other=False) | Q(hidden=False), name="fee_types_other_never_hidden"
            ),
        )

    def __str__(self) -> str:
        return self.name
