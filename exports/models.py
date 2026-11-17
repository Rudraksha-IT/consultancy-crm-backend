"""Export Records: who downloaded which list as a CSV file, and when (spec 31c; GLOSSARY).

Records are only ever added, by `exports.services`. Saving an existing record, or deleting or
bulk-updating records, raises `ExportRecordIsAppendOnlyError`.
"""

from __future__ import annotations

from typing import Any

from django.db import models
from django.utils import timezone

from exports.exceptions import ExportRecordIsAppendOnlyError
from staff.models import STAFF_NAME_MAX_LENGTH, Staff

LIST_MAX_LENGTH = 20


class ExportList(models.TextChoices):
    """The lists that can be exported, including the Clock-in team view (issue 56)."""

    STUDENTS = "students", "Students"
    APPLICATIONS = "applications", "Applications"
    VISAS = "visas", "Visas"
    CLOCK_IN = "clock_in", "Clock-in"


class AppendOnlyQuerySet(models.QuerySet["ExportRecord"]):
    """Refuses bulk changes and bulk deletes."""

    def update(self, **kwargs: Any) -> int:  # noqa: ANN401  # Django's own signature
        """Refuse: an Export Record is never changed."""
        raise ExportRecordIsAppendOnlyError

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse: an Export Record is never deleted."""
        raise ExportRecordIsAppendOnlyError


class ExportRecord(models.Model):
    """One CSV export of one list."""

    list = models.CharField(max_length=LIST_MAX_LENGTH, choices=ExportList.choices)
    # The list's filters as they were checked and used, e.g. {"stage": "applying"}.
    filters = models.JSONField(default=dict)
    # The same filters in words, as they read at the time, e.g. "Stage: Applying".
    filters_text = models.TextField()
    row_count = models.PositiveIntegerField()
    at = models.DateTimeField(default=timezone.now)
    # Who exported. The name is kept as it was; a Platform Admin is never linked as Staff.
    by_staff = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    by_platform_admin = models.BooleanField(default=False)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ("-at", "-pk")
        indexes = (models.Index(fields=("-at", "-id")),)

    def __str__(self) -> str:
        return f"{self.list} by {self.by_name}"

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # Django's own signature
        """Add a new record; refuse to change one already stored."""
        if self.pk is not None:
            raise ExportRecordIsAppendOnlyError
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:  # noqa: ANN401
        """Refuse: an Export Record is never deleted."""
        raise ExportRecordIsAppendOnlyError
