"""A Visit: one time a Student came to the office (GLOSSARY: Visit; spec 23a-23c)."""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from config.public_ids import public_id_field
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student

KIND_MAX_LENGTH = 20
# The reason's words, a colon and up to 200 characters of details.
PURPOSE_MAX_LENGTH = 300


class VisitKind(models.TextChoices):
    """A Student's first visit, or a later one."""

    NEW = "new", "New"
    FOLLOW_UP = "follow_up", "Follow-up"


class VisitReason(models.TextChoices):
    """Why a returning Student came in, as Front Desk picks it."""

    COUNSELLING_SESSION = "counselling_session", "Counselling session"
    BRINGING_DOCUMENTS = "bringing_documents", "Bringing documents"
    MAKING_A_PAYMENT = "making_a_payment", "Making a payment"
    ATTENDING_A_CLASS = "attending_a_class", "Attending a class"
    ASKING_ABOUT_PROGRESS = "asking_about_progress", "Asking about progress"
    OTHER = "other", "Other"


class Visit(models.Model):
    """One time a Student came to the office, and why."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="visits")
    at = models.DateTimeField(default=timezone.now)
    kind = models.CharField(max_length=KIND_MAX_LENGTH, choices=VisitKind.choices)
    purpose = models.CharField(max_length=PURPOSE_MAX_LENGTH, blank=True, default="")
    # Who logged it. The name is kept as it was, so renaming or deactivating them changes nothing.
    recorded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    recorded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)

    class Meta:
        ordering = ("-at", "-pk")
        indexes = (
            models.Index(fields=("-at", "-id")),
            models.Index(fields=("student", "-at", "-id")),
        )

    def __str__(self) -> str:
        return f"{self.kind} visit by student {self.student_id}"
