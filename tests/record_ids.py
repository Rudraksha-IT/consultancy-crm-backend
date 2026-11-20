"""Checks that an API answer names records only by UUID, never by database number (ADR 0027)."""

from __future__ import annotations

import re
import uuid

# Keys that name a record: "id", "student_id", "attachment_ids", ...
_ID_SUFFIXES = ("_id", "_ids")
_LIST_INDEX = re.compile(r"\[\d+\]")

# Records ticket 17 hasn't switched yet: employees and the office's other records (ticket 18),
# the Platform and logins (ticket 19). Each ticket removes its own; the set ends empty.
SWITCHED_LATER = frozenset(
    {
        "assigned_counsellor.id",
        "university.id",
        "course.id",
        "document_type.id",
        "fee_type.id",
        "uploaded_by.id",
        "recorded_by.id",
        "sent_by.id",
        "added_by.id",
        "created_by.id",
        "assignee.id",
        "by.id",
        "viewer_id",
    }
)


def _names_a_record(key: str) -> bool:
    return key == "id" or key.endswith(_ID_SUFFIXES)


def _is_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def record_numbers_in(answer: object, where: str = "") -> list[str]:
    """Return where in `answer` a record is named by a number (`results[0].student_id`)."""
    if isinstance(answer, dict):
        return _numbers_in_object(answer, where)
    if isinstance(answer, list):
        return [
            place
            for index, item in enumerate(answer)
            for place in record_numbers_in(item, f"{where}[{index}]")
        ]
    return []


def _numbers_in_object(answer: dict[object, object], where: str) -> list[str]:
    found: list[str] = []
    for key, value in answer.items():
        place = f"{where}.{key}" if where else str(key)
        values = value if isinstance(value, list) else [value]
        if _names_a_record(str(key)) and any(_is_number(item) for item in values):
            found.append(place)
        found.extend(record_numbers_in(value, place))
    return found


def _switched_later(place: str) -> bool:
    plain = _LIST_INDEX.sub("", place)
    return any(plain == key or plain.endswith(f".{key}") for key in SWITCHED_LATER)


def assert_no_record_numbers(answer: object) -> None:
    """Fail naming every place in `answer` where a record is named by a number.

    Places in `SWITCHED_LATER` are left for the ticket that switches them.
    """
    found = [place for place in record_numbers_in(answer) if not _switched_later(place)]
    assert not found, f"record numbers in the answer: {found}"


def is_uuid(value: object) -> bool:
    """Whether `value` is a UUID written as text."""
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
