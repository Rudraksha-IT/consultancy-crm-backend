"""The test helper that finds record numbers in an API answer (ADR 0027)."""

from __future__ import annotations

import uuid

import pytest

from tests.record_ids import assert_no_record_numbers, is_uuid, record_numbers_in

A_UUID = str(uuid.uuid4())


def test_finds_numbers_under_id_keys_at_any_depth() -> None:
    """`id`, `…_id` and `…_ids` holding a number are found, however deep."""
    answer = {
        "id": 16,
        "assigned_counsellor": {"id": 3, "name": "Asha"},
        "results": [{"student_id": 9}, {"student_id": A_UUID}],
        "attachment_ids": [A_UUID, 4],
    }
    assert record_numbers_in(answer) == [
        "id",
        "assigned_counsellor.id",
        "results[0].student_id",
        "attachment_ids",
    ]


def test_uuids_counts_and_flags_pass() -> None:
    """UUIDs, counts, amounts, flags and empty references are fine."""
    answer = {
        "id": A_UUID,
        "count": 12,
        "version": 3,
        "is_paid": True,
        "student_id": None,
        "items": [{"id": A_UUID, "amount": 40000}],
    }
    assert record_numbers_in(answer) == []


def test_a_list_answer_is_walked() -> None:
    """An answer that is a list is checked item by item."""
    assert record_numbers_in([{"id": 1}, {"id": A_UUID}]) == ["[0].id"]


def test_is_uuid() -> None:
    """Only a UUID written as text counts."""
    assert is_uuid(A_UUID)
    assert not is_uuid("16")
    assert not is_uuid(16)
    assert not is_uuid("not-a-uuid")


def test_assert_names_every_number_nested_too() -> None:
    """An employee's number is refused as much as a Student's; UUIDs pass."""
    assert_no_record_numbers({"results": [{"id": A_UUID, "assigned_counsellor": {"id": A_UUID}}]})
    with pytest.raises(AssertionError, match=r"results\[0\]\.assigned_counsellor\.id"):
        assert_no_record_numbers({"results": [{"id": A_UUID, "assigned_counsellor": {"id": 3}}]})
