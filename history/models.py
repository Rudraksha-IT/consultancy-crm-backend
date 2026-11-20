"""History: who changed what on a Student, and when (spec 45; GLOSSARY: History).

Entries are only ever added, by services. Saving an existing entry, or deleting or bulk-updating
entries, raises `HistoryIsAppendOnlyError`.
"""

from __future__ import annotations

from typing import Any

from django.db import models
from django.utils import timezone

from history.exceptions import HistoryIsAppendOnlyError
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student

KIND_MAX_LENGTH = 30


class HistoryKind(models.TextChoices):
    """What kind of change an entry records."""

    NOTE_EDITED = "note_edited", "Note edited"
    # Before and after are Stage values; `detail` is the reason when the Student was lost.
    STAGE_CHANGED = "stage_changed", "Stage changed"
    # After is the counsellor's name as it was then.
    COUNSELLOR_ASSIGNED = "counsellor_assigned", "Counsellor assigned"
    # Before and after are the counsellors' names as they were then.
    TRANSFERRED = "transferred", "Transferred"
    # After is the amount, Fee Type (with its note) and method; `detail` is empty.
    PAYMENT_RECORDED = "payment_recorded", "Payment recorded"
    # Before and after name only what changed; `detail` is empty (issue 19).
    PAYMENT_EDITED = "payment_edited", "Payment edited"
    # Before is the deleted Payment in full; `detail` is empty (issue 19).
    PAYMENT_DELETED = "payment_deleted", "Payment deleted"
    # After is the Fee, e.g. "Visa processing, NPR 40,000" (issue 35).
    FEE_ADDED = "fee_added", "Fee added"
    # Before and after are the Fee as it was and as it is now (issue 35).
    FEE_EDITED = "fee_edited", "Fee edited"
    # Before is the deleted Fee (issue 35).
    FEE_DELETED = "fee_deleted", "Fee deleted"
    # After is "<University>, <Conditional|Unconditional>" (issue 76).
    OFFER_LETTER_ADDED = "offer_letter_added", "Offer letter added"
    # Before and after are the Universities of the old and new Chosen Offer, empty for none;
    # `detail` says what caused it when it was not chosen by hand (issue 76).
    CHOSEN_OFFER_CHANGED = "chosen_offer_changed", "Chosen offer changed"
    # After names what: "Passport", or "page-2.pdf of Passport" for one file; `detail` is why it
    # was put in the Bin (issue 79). The same for the four kinds below.
    DOCUMENT_BINNED = "document_binned", "Put in the Bin"
    DOCUMENT_TAKEN_OUT = "document_taken_out", "Taken out of the Bin"
    DOCUMENT_RESTORED = "document_restored", "Restored from the Bin"
    DOCUMENT_DELETED_FROM_BIN = "document_deleted_from_bin", "Deleted for good from the Bin"
    # An Admin's own delete, which skips the Bin; `detail` is empty.
    DOCUMENT_DELETED = "document_deleted", "Document deleted"
    # Issue 83, recorded from when it shipped (2026-11-15), nothing guessed for the past.
    # Before and after are Application Statuses set by hand; `detail` is the University.
    APPLICATION_STATUS_CHANGED = "application_status_changed", "Application status changed"
    # Before and after are Visa Statuses; `detail` is the University.
    VISA_STATUS_CHANGED = "visa_status_changed", "Visa status changed"
    # After is the Task's title, for a Task about the Student.
    TASK_DONE = "task_done", "Task done"
    # After is the Document Type's name; for Rejected, `detail` is the reason.
    DOCUMENT_VERIFIED = "document_verified", "Document verified"
    DOCUMENT_REJECTED = "document_rejected", "Document rejected"
    # Before and after are the appointment's moments in UTC ISO 8601, empty for none (booked:
    # before empty; moved: both; cleared: after empty); `detail` is the Appointment.
    VISA_DATE_CHANGED = "visa_date_changed", "Visa date changed"


class AppendOnlyQuerySet(models.QuerySet["HistoryEntry"]):
    """Refuses bulk changes and bulk deletes."""

    def update(self, **kwargs: Any) -> int:  # noqa: ANN401  # Django's own signature
        """Refuse: History is never changed."""
        raise HistoryIsAppendOnlyError

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse: History is never deleted."""
        raise HistoryIsAppendOnlyError


class HistoryEntry(models.Model):
    """One recorded change to a Student."""

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="history")
    kind = models.CharField(max_length=KIND_MAX_LENGTH, choices=HistoryKind.choices)
    at = models.DateTimeField(default=timezone.now)
    # Who did it. The name is kept as it was, so renaming or deactivating them changes nothing.
    by_staff = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    # Set for changes a Platform Admin makes during Support Access (issue 27).
    by_platform_admin = models.BooleanField(default=False)
    # The value before and after the change, e.g. a Note's previous and new text.
    before = models.TextField(blank=True, default="")
    after = models.TextField(blank=True, default="")
    # More about the change, e.g. why a Student was marked Lost.
    detail = models.TextField(blank=True, default="")

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ("-at", "-pk")
        indexes = (models.Index(fields=("student", "-at", "-id")),)

    def __str__(self) -> str:
        return f"{self.kind} by {self.by_name}"

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # Django's own signature
        """Add a new entry; refuse to change one already stored."""
        if self.pk is not None:
            raise HistoryIsAppendOnlyError
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:  # noqa: ANN401
        """Refuse: History is never deleted."""
        raise HistoryIsAppendOnlyError
