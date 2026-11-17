"""The shared StrictSerializer refuses unknown fields without repeating their names."""

from __future__ import annotations

import pytest
from rest_framework import serializers

from config.serializers import StrictSerializer
from tests.integration.test_stage_and_counsellor_integration import INJECTION_STRINGS

UNKNOWN = {"detail": ["Unknown field."]}


class _Name(StrictSerializer[None]):
    name = serializers.CharField()


class _Wrapper(StrictSerializer[None]):
    person = _Name()


def errors_for(serializer: serializers.Serializer[None]) -> object:
    """The errors of a serializer that must refuse its data."""
    assert not serializer.is_valid()
    return serializer.errors


class TestUnknownFields:
    def test_an_unknown_field_is_refused_without_its_name(self) -> None:
        assert errors_for(_Name(data={"name": "Sita", "is_admin": True})) == UNKNOWN

    @pytest.mark.parametrize("key", INJECTION_STRINGS)
    def test_an_injection_shaped_name_is_never_sent_back(self, key: str) -> None:
        errors = errors_for(_Name(data={"name": "Sita", key: "1"}))

        assert errors == UNKNOWN
        assert key not in str(errors)

    def test_several_unknown_fields_give_one_message(self) -> None:
        assert errors_for(_Name(data={"a": 1, "b": 2})) == UNKNOWN

    def test_unknown_fields_inside_a_nested_object_are_refused_on_that_object(self) -> None:
        errors = errors_for(_Wrapper(data={"person": {"name": "Sita", "<b>x</b>": 1}}))

        assert errors == {"person": UNKNOWN}

    def test_known_fields_still_pass(self) -> None:
        serializer = _Name(data={"name": "Sita"})

        assert serializer.is_valid()
        assert serializer.validated_data == {"name": "Sita"}
