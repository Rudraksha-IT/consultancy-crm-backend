from __future__ import annotations

from enum import StrEnum


class Module(StrEnum):
    """A feature area of the product, always on or switched per Consultancy; in display order."""

    STUDENTS = "students"
    STAFF = "staff"
    VISITS = "visits"
    TASKS = "tasks"
    HISTORY = "history"
    CATALOGUE = "catalogue"
    APPLICATIONS = "applications"
    VISAS = "visas"
    DOCUMENTS = "documents"
    PAYMENTS = "payments"
    CONFIGURATION = "configuration"
    BATCHES = "batches"
    COMMUNICATION = "communication"
    ACCOUNTS = "accounts"
    REPORTS = "reports"
    CLOCK_IN = "clock_in"


# A Platform Admin switches these per Consultancy (shown as "Features"); every other Module is
# always on and can't be switched off.
SWITCHABLE_MODULES: tuple[Module, ...] = (
    Module.BATCHES,
    Module.COMMUNICATION,
    Module.ACCOUNTS,
    Module.REPORTS,
    Module.CLOCK_IN,
)
ALWAYS_ON_MODULES: tuple[Module, ...] = tuple(
    module for module in Module if module not in SWITCHABLE_MODULES
)
