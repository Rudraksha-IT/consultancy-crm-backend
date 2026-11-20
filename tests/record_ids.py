"""Checks that an API answer names records only by UUID, never by database number (ADR 0027)."""

from __future__ import annotations

import uuid

# Keys that name a record: "id", "student_id", "attachment_ids", ...
_ID_SUFFIXES = ("_id", "_ids")


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


def assert_no_record_numbers(answer: object) -> None:
    """Fail naming every place in `answer` where a record is named by a number."""
    found = record_numbers_in(answer)
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
