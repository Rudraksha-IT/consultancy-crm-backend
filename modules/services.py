"""The one place that answers "which Modules are on for this Consultancy?"."""

from __future__ import annotations

import logging
from collections.abc import Collection

from modules.exceptions import AlwaysOnModuleError
from modules.types import SWITCHABLE_MODULES, Module
from tenancy.models import Consultancy

logger = logging.getLogger(__name__)


def is_module_on(consultancy: Consultancy, module: Module) -> bool:
    """Return whether a Module is on for a Consultancy; always-on Modules always are."""
    return module not in SWITCHABLE_MODULES or module in _switched_on(consultancy)


def get_enabled_modules(consultancy: Consultancy) -> list[Module]:
    """Return the Modules on for a Consultancy, in display order."""
    return [module for module in Module if is_module_on(consultancy, module)]


def switch_modules(consultancy: Consultancy, switched_on: Collection[Module]) -> None:
    """Switch on exactly `switched_on` of the switchable Modules and the rest off.

    Switching a Module off only hides it: its data is kept and comes back when it is on again.

    Raises:
        AlwaysOnModuleError: `switched_on` names a Module that isn't switchable.
    """
    if any(module not in SWITCHABLE_MODULES for module in switched_on):
        raise AlwaysOnModuleError
    before = _switched_on(consultancy)
    consultancy.switched_on_modules = [m.value for m in SWITCHABLE_MODULES if m in switched_on]
    consultancy.save(update_fields=["switched_on_modules"])
    for module in SWITCHABLE_MODULES:
        is_on = module in switched_on
        if is_on != (module in before):
            logger.info(
                "Module switched: consultancy_id=%s, module=%s, on=%s",
                consultancy.pk,
                module.value,
                is_on,
            )


def _switched_on(consultancy: Consultancy) -> frozenset[Module]:
    """Read the stored switches; anything that isn't a switchable Module's value is ignored."""
    stored: object = consultancy.switched_on_modules
    if not isinstance(stored, list):
        return frozenset()
    return frozenset(module for module in SWITCHABLE_MODULES if module.value in stored)
