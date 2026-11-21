"""Every email sent to a Student (ADR 0023; spec 2026-11-18 story 51).

Past SMS records stay readable; no SMS is ever sent again.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils import timezone

from config.public_ids import public_id_field
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student

CHOICE_MAX_LENGTH = 10
ERROR_MAX_LENGTH = 200
SENT_TO_MAX_LENGTH = 254
SUBJECT_MAX_LENGTH = 150


class Channel(models.TextChoices):
    """How a message was sent. Only email now; SMS records are kept from before."""

    SMS = "sms", "SMS (no longer available)"
    EMAIL = "email", "Email"


class MessageStatus(models.TextChoices):
    """Whether an email went out."""

    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class Message(models.Model):
    """One email to one Student, sent or failed. Never changed afterwards."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="messages")
    channel = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=Channel.choices)
    # Blank on past SMS records, which had none.
    subject = models.CharField(max_length=SUBJECT_MAX_LENGTH, blank=True, default="")
    body = models.TextField()
    status = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=MessageStatus.choices)
    # Why it failed, in words safe to show; blank when sent.
    error = models.CharField(max_length=ERROR_MAX_LENGTH, blank=True, default="")
    # The address (or, for a past SMS, the number) it went to, as it was then.
    sent_to = models.CharField(max_length=SENT_TO_MAX_LENGTH, blank=True, default="")
    # The names of the files sent with it, in order (spec story 51); the files stay where they are.
    attachments = models.JSONField(default=list, blank=True)
    # None when a Platform Admin did it during Support Access; the name says who (issue 27).
    sent_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # Who sent it, as they were named then.
    sent_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, default="")
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-sent_at", "-pk")
        indexes = (models.Index(fields=("student", "-sent_at"), name="communication_by_student"),)
        constraints = (
            models.CheckConstraint(
                condition=Q(channel__in=Channel.values), name="communication_known_channel"
            ),
            models.CheckConstraint(
                condition=Q(status__in=MessageStatus.values), name="communication_known_status"
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_channel_display()} to {self.student.name}: {self.get_status_display()}"
