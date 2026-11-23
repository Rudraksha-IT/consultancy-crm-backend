from __future__ import annotations


class TooManyOfficeNetworksError(Exception):
    """The office already has as many Office Networks as it may (OFFICE_NETWORKS_MAX)."""


class OfficeNetworkOverlapsError(Exception):
    """The network is, holds or sits inside one already on the list."""
