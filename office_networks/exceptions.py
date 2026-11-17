from __future__ import annotations


class OfficeNetworkChangeIsAppendOnlyError(Exception):
    """Something tried to change or delete an Office Network Change. They are only ever added."""

    def __init__(self) -> None:
        super().__init__("Office network changes can't be changed or deleted.")


class TooManyOfficeNetworksError(Exception):
    """The office already has as many Office Networks as it may (OFFICE_NETWORKS_MAX)."""


class OfficeNetworkOverlapsError(Exception):
    """The network is, holds or sits inside one already on the list."""
