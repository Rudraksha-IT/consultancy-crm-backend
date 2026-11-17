from __future__ import annotations

from django.apps import AppConfig


class OfficeNetworksConfig(AppConfig):
    """The office's Office Networks, from which Staff may clock in (spec 104-105, issue 55)."""

    name = "office_networks"
