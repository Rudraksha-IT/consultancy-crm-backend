"""An Inbox message: plain text from one Staff member to another (spec 128-130; GLOSSARY).

Private to its two people: no Admin, Platform Admin, report, export or log reads its text.
Messages are never edited or deleted, so there is nothing to change but `read_at`.
"""

from __future__ import annotations

from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Length
from django.db.models.lookups import LessThanOrEqual
from django.utils import timezone

from staff.models import Staff

# The longest message, in characters.
INBOX_MESSAGE_MAX_LENGTH = 2000


class InboxMessage(models.Model):
    """One message from `sender` to `recipient`, unread until the recipient opens it."""

    sender = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    recipient = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    text = models.CharField(max_length=INBOX_MESSAGE_MAX_LENGTH)
    sent_at = models.DateTimeField(default=timezone.now)
    # When the recipient opened the conversation; None while it is unread.
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("sent_at", "pk")
        indexes = (
            models.Index(fields=("sender", "recipient", "-sent_at"), name="inbox_sent"),
            models.Index(fields=("recipient", "-sent_at"), name="inbox_received"),
            # The menu count: my unread messages, kept small by leaving out read ones.
            models.Index(
                fields=("recipient", "sender"),
                condition=Q(read_at__isnull=True),
                name="inbox_unread",
            ),
        )
        constraints = (
            models.CheckConstraint(condition=~Q(text=""), name="inbox_text_given"),
            models.CheckConstraint(
                condition=LessThanOrEqual(Length("text"), INBOX_MESSAGE_MAX_LENGTH),
                name="inbox_text_length",
            ),
            models.CheckConstraint(
                condition=~Q(sender=F("recipient")), name="inbox_not_to_yourself"
            ),
        )

    def __str__(self) -> str:
        # Never the text: it is private to the two people.
        return f"Inbox message {self.pk}"
