"""Public services for the Inbox (spec 128-130). Call inside the Consultancy's schema.

Every query is about `me`: a conversation is only ever read by one of its two people. Who may
use the Inbox at all is the views' job, through `access.policy`. Nothing here logs message text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db.models import Case, Count, F, IntegerField, Max, Q, QuerySet, When
from django.utils import timezone

from inbox.exceptions import PersonHasLeftError
from inbox.models import InboxMessage
from staff.models import Staff, StaffStatus

# The newest messages a conversation shows; older ones stay stored.
THREAD_LIMIT = 200
# How much of the last message the conversation list shows.
PREVIEW_LENGTH = 120

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboxCheck:
    """The polling answer: conversations with unread messages, and my newest message's id."""

    unread: int
    latest: int | None


@dataclass(frozen=True)
class Conversation:
    """One row of my conversation list."""

    person: Staff
    last: InboxMessage
    unread: bool


@dataclass(frozen=True)
class Thread:
    """One conversation, oldest first, and whether older messages were left out."""

    person: Staff
    messages: list[InboxMessage]
    more: bool


def can_write_to(person: Staff) -> bool:
    """Return whether `person` can still be written to: only while they work here."""
    return person.status == StaffStatus.ACTIVE


def preview(text: str) -> str:
    """The start of a message for the conversation list, cut on one line."""
    line = " ".join(text.split())
    if len(line) <= PREVIEW_LENGTH:
        return line
    return f"{line[: PREVIEW_LENGTH - 1].rstrip()}…"


def find_person(me: Staff, person_id: int) -> Staff:
    """Someone `me` can have a conversation with: anyone else who has joined the office.

    Raises:
        Staff.DoesNotExist: Unknown, `me`, or invited and not joined yet.
    """
    return Staff.objects.exclude(pk=me.pk).exclude(status=StaffStatus.INVITED).get(pk=person_id)


def people_to_write_to(me: Staff) -> QuerySet[Staff]:
    """The "New message" list: everyone active in the office except `me`, by name."""
    return Staff.objects.filter(status=StaffStatus.ACTIVE).exclude(pk=me.pk).order_by("name", "pk")


def check(me: Staff) -> InboxCheck:
    """How many conversations have messages unread by `me`, and the newest message's id."""
    found = _mine(me).aggregate(
        latest=Max("pk"),
        unread=Count("sender", distinct=True, filter=Q(recipient=me, read_at__isnull=True)),
    )
    return InboxCheck(unread=found["unread"], latest=found["latest"])


def conversations(me: Staff) -> list[Conversation]:
    """Every conversation `me` has, newest first, each with its last message."""
    lasts = list(
        _mine(me)
        .annotate(other=_other_person(me))
        .order_by("other", "-sent_at", "-pk")
        .distinct("other")
        .select_related("sender", "recipient")
    )
    unread_from = set(
        InboxMessage.objects.filter(recipient=me, read_at__isnull=True).values_list(
            "sender_id", flat=True
        )
    )
    found = []
    for last in lasts:
        person = last.recipient if last.sender_id == me.pk else last.sender
        found.append(Conversation(person=person, last=last, unread=person.pk in unread_from))
    return sorted(found, key=lambda one: (one.last.sent_at, one.last.pk), reverse=True)


def open_conversation(me: Staff, person: Staff) -> Thread:
    """The conversation between `me` and `person`, newest `THREAD_LIMIT`; marks theirs read."""
    InboxMessage.objects.filter(sender=person, recipient=me, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    between = InboxMessage.objects.filter(
        Q(sender=me, recipient=person) | Q(sender=person, recipient=me)
    )
    newest = list(between.order_by("-sent_at", "-pk")[: THREAD_LIMIT + 1])
    return Thread(
        person=person,
        messages=list(reversed(newest[:THREAD_LIMIT])),
        more=len(newest) > THREAD_LIMIT,
    )


def send(me: Staff, person: Staff, text: str) -> InboxMessage:
    """Store a message from `me` to `person`.

    Raises:
        PersonHasLeftError: `person` no longer works here.
    """
    if not can_write_to(person):
        raise PersonHasLeftError
    message = InboxMessage.objects.create(sender=me, recipient=person, text=text)
    # Who and when only: the text is private to the two people.
    logger.info(
        "Inbox message sent: message_id=%s, from_staff_id=%s, to_staff_id=%s",
        message.pk,
        me.pk,
        person.pk,
    )
    return message


def _mine(me: Staff) -> QuerySet[InboxMessage]:
    return InboxMessage.objects.filter(Q(sender=me) | Q(recipient=me))


def _other_person(me: Staff) -> Case:
    return Case(
        When(sender=me, then=F("recipient_id")),
        default=F("sender_id"),
        output_field=IntegerField(),
    )
