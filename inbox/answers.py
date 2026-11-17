"""What the Inbox endpoints answer. Each message is described only to one of its two people."""

from __future__ import annotations

from typing import Any

from inbox.models import InboxMessage
from inbox.services import Conversation, InboxCheck, Thread, can_write_to, preview
from staff.models import Staff


def person_answer(person: Staff) -> dict[str, Any]:
    """Who the conversation is with."""
    return {
        "id": person.pk,
        "name": person.name,
        "job_title": person.job_title,
        "has_left": not can_write_to(person),
    }


def message_answer(message: InboxMessage, me: Staff) -> dict[str, Any]:
    """One message, as `me` sees it."""
    return {
        "id": message.pk,
        "text": message.text,
        "from_me": message.sender_id == me.pk,
        "sent_at": message.sent_at.isoformat(),
    }


def conversations_answer(found: list[Conversation], me: Staff) -> dict[str, Any]:
    """My conversation list, newest first."""
    return {
        "conversations": [
            {
                "person": person_answer(one.person),
                "last": {
                    "text": preview(one.last.text),
                    "from_me": one.last.sender_id == me.pk,
                    "sent_at": one.last.sent_at.isoformat(),
                },
                "unread": one.unread,
            }
            for one in found
        ]
    }


def thread_answer(thread: Thread, me: Staff) -> dict[str, Any]:
    """One conversation, oldest first."""
    return {
        "person": person_answer(thread.person),
        "can_write": can_write_to(thread.person),
        "messages": [message_answer(message, me) for message in thread.messages],
        "more": thread.more,
    }


def check_answer(found: InboxCheck) -> dict[str, Any]:
    """The polling answer: never any text."""
    return {"unread": found.unread, "latest": found.latest}


def people_answer(people: list[Staff]) -> dict[str, Any]:
    """Who I can start a conversation with."""
    return {
        "people": [
            {"id": person.pk, "name": person.name, "job_title": person.job_title}
            for person in people
        ]
    }
