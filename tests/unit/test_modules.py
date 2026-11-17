"""The one answer to "is Module X on for this Consultancy?", from the stored switches."""

from __future__ import annotations

import pytest

from modules.exceptions import AlwaysOnModuleError
from modules.services import get_enabled_modules, is_module_on, switch_modules
from modules.types import ALWAYS_ON_MODULES, SWITCHABLE_MODULES, Module
from tenancy.models import Consultancy


def consultancy_with(stored: object) -> Consultancy:
    """Return an unsaved Consultancy whose stored switches are `stored`."""
    return Consultancy(name="ABC Education", schema_name="abcedu", switched_on_modules=stored)


class TestModuleSwitches:
    def test_every_module_is_either_always_on_or_switchable(self) -> None:
        assert set(ALWAYS_ON_MODULES) | set(SWITCHABLE_MODULES) == set(Module)
        assert not set(ALWAYS_ON_MODULES) & set(SWITCHABLE_MODULES)

    def test_the_switchable_modules_are_batches_communication_accounts_reports_and_clock_in(
        self,
    ) -> None:
        assert SWITCHABLE_MODULES == (
            Module.BATCHES,
            Module.COMMUNICATION,
            Module.ACCOUNTS,
            Module.REPORTS,
            Module.CLOCK_IN,
        )

    @pytest.mark.parametrize("module", ALWAYS_ON_MODULES)
    def test_always_on_modules_are_on_with_nothing_switched_on(self, module: Module) -> None:
        assert is_module_on(consultancy_with([]), module)

    @pytest.mark.parametrize("module", SWITCHABLE_MODULES)
    def test_switchable_modules_follow_their_switch(self, module: Module) -> None:
        assert not is_module_on(consultancy_with([]), module)
        assert is_module_on(consultancy_with([module.value]), module)

    def test_enabled_modules_keep_display_order(self) -> None:
        enabled = get_enabled_modules(consultancy_with(["reports", "batches"]))

        assert enabled == [*ALWAYS_ON_MODULES, Module.BATCHES, Module.REPORTS]

    @pytest.mark.parametrize(
        "stored",
        [None, "batches", 1, {"batches": True}, ["unknown", 3, None], ["Batches"]],
    )
    def test_a_damaged_stored_value_switches_nothing_on(self, stored: object) -> None:
        assert get_enabled_modules(consultancy_with(stored)) == list(ALWAYS_ON_MODULES)

    def test_switching_an_always_on_module_is_refused_before_anything_is_stored(self) -> None:
        consultancy = consultancy_with([])

        with pytest.raises(AlwaysOnModuleError):
            switch_modules(consultancy, [Module.BATCHES, Module.STUDENTS])

        assert consultancy.switched_on_modules == []
