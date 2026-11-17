from __future__ import annotations

from collections.abc import Mapping

from rest_framework import serializers

from config.serializers import UNKNOWN_FIELD_KEY
from modules.types import ALWAYS_ON_MODULES, SWITCHABLE_MODULES, Module

ALWAYS_ON_MESSAGE = "This is always on and can't be switched off."
UNKNOWN_MODULE_MESSAGE = "Unknown Module."
NOT_A_BOOLEAN_MESSAGE = "Must be true or false."
_ALWAYS_ON_VALUES = frozenset(module.value for module in ALWAYS_ON_MODULES)


class StrictBooleanField(serializers.Field[bool, bool, bool, object]):
    """Only JSON true or false; DRF's BooleanField would also take "true", 1 or "on"."""

    def validate_empty_values(self, data: object) -> tuple[bool, object]:
        """Refuse null with the same message; a missing value is still "required"."""
        if data is None:
            raise serializers.ValidationError(NOT_A_BOOLEAN_MESSAGE)
        return super().validate_empty_values(data)

    def to_internal_value(self, data: object) -> bool:
        """Return the boolean, refusing anything else."""
        if not isinstance(data, bool):
            raise serializers.ValidationError(NOT_A_BOOLEAN_MESSAGE)
        return bool(data)


class ModuleSwitchesSerializer(serializers.Serializer[None]):
    """Every switchable Module with true (on) or false (off), and nothing else."""

    batches = StrictBooleanField()
    communication = StrictBooleanField()
    accounts = StrictBooleanField()
    reports = StrictBooleanField()
    clock_in = StrictBooleanField()

    def to_internal_value(self, data: object) -> dict[str, bool]:
        """Refuse always-on and unknown Modules before checking the switches.

        An always-on Module is named (its name is ours); an unknown one is not repeated back.
        """
        if isinstance(data, Mapping):
            extra = [key for key in data if key not in self.fields]
            if any(key not in _ALWAYS_ON_VALUES for key in extra):
                raise serializers.ValidationError({UNKNOWN_FIELD_KEY: [UNKNOWN_MODULE_MESSAGE]})
            if extra:
                raise serializers.ValidationError({key: [ALWAYS_ON_MESSAGE] for key in extra})
        validated: dict[str, bool] = super().to_internal_value(data)
        return validated

    def switched_on(self) -> list[Module]:
        """Return the switchable Modules set to on."""
        return [module for module in SWITCHABLE_MODULES if self.validated_data[module.value]]
