"""The office's SMS and email accounts, and every message sent to a Student (spec 88-91)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils import timezone

from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student

SMS_SENDER_MAX_LENGTH = 11
EMAIL_HOST_MAX_LENGTH = 253
EMAIL_ADDRESS_MAX_LENGTH = 254
CHOICE_MAX_LENGTH = 10
ERROR_MAX_LENGTH = 200
SENT_TO_MAX_LENGTH = 254
# SMTP with STARTTLS (587, 25, 2525) or TLS from the start (465); nothing else is ever dialled.
EMAIL_PORTS = (587, 465, 25, 2525)
DEFAULT_EMAIL_PORT = 587
# The one row of provider settings each Consultancy schema has.
SETTINGS_ROW = 1


class SmsProvider(models.TextChoices):
    """The SMS companies an office can send through (the prototype's list)."""

    SPARROW = "sparrow", "Sparrow SMS"
    AAKASH = "aakash", "Aakash SMS"


class Channel(models.TextChoices):
    """How a message is sent."""

    SMS = "sms", "SMS"
    EMAIL = "email", "Email"


class MessageStatus(models.TextChoices):
    """Whether a message went out (spec 91)."""

    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class ProviderSettings(models.Model):
    """The office's own SMS and email accounts (spec 88). Secrets are stored sealed only.

    A blank `sms_provider` or `email_host` means that channel isn't set up.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=SETTINGS_ROW)
    sms_provider = models.CharField(
        max_length=CHOICE_MAX_LENGTH, choices=SmsProvider.choices, blank=True, default=""
    )
    sms_sender = models.CharField(max_length=SMS_SENDER_MAX_LENGTH, blank=True, default="")
    sms_token_sealed = models.TextField(blank=True, default="")
    email_host = models.CharField(max_length=EMAIL_HOST_MAX_LENGTH, blank=True, default="")
    email_port = models.PositiveIntegerField(default=DEFAULT_EMAIL_PORT)
    email_username = models.CharField(max_length=EMAIL_ADDRESS_MAX_LENGTH, blank=True, default="")
    email_password_sealed = models.TextField(blank=True, default="")
    email_from = models.EmailField(max_length=EMAIL_ADDRESS_MAX_LENGTH, blank=True, default="")
    updated_at = models.DateTimeField(default=timezone.now)
    # None when a Platform Admin did it during Support Access (issue 27).
    updated_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")

    class Meta:
        constraints = (
            models.CheckConstraint(condition=Q(id=SETTINGS_ROW), name="communication_one_row"),
            models.CheckConstraint(
                condition=Q(sms_provider__in=["", *SmsProvider.values]),
                name="communication_known_sms_provider",
            ),
            models.CheckConstraint(
                condition=Q(email_port__in=EMAIL_PORTS), name="communication_known_email_port"
            ),
        )

    def __str__(self) -> str:
        return "SMS and email settings"


class Message(models.Model):
    """One SMS or email to one Student, sent or failed (spec 89, 91). Never changed afterwards."""

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="messages")
    channel = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=Channel.choices)
    body = models.TextField()
    status = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=MessageStatus.choices)
    # Why it failed, in words safe to show; blank when sent.
    error = models.CharField(max_length=ERROR_MAX_LENGTH, blank=True, default="")
    # The number or address it went to, as it was then.
    sent_to = models.CharField(max_length=SENT_TO_MAX_LENGTH, blank=True, default="")
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
