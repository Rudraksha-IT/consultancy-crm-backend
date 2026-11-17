"""The Office Network limits, read from the environment (ADR 0004 rule 5, issue 55).

Ranges can only be made narrower than ADR 0004 allows (IPv4 /24, IPv6 /48), never wider.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class OfficeNetworkLimits:
    """How many Office Networks an office may have, and the widest range each may be."""

    max_networks: int
    ipv4_widest_prefix: int
    ipv6_widest_prefix: int


@dataclass(frozen=True)
class _Setting:
    name: str
    default: int
    lowest: int
    highest: int

    def read(self, environ: Mapping[str, str]) -> int:
        value = environ.get(self.name, str(self.default))
        if not value.isdigit() or not self.lowest <= int(value) <= self.highest:
            message = f"{self.name} must be a whole number from {self.lowest} to {self.highest}."
            raise ImproperlyConfigured(message)
        return int(value)


MAX_NETWORKS = _Setting("OFFICE_NETWORKS_MAX", default=20, lowest=1, highest=100)
IPV4_WIDEST_PREFIX = _Setting(
    "OFFICE_NETWORK_IPV4_WIDEST_PREFIX", default=24, lowest=24, highest=32
)
IPV6_WIDEST_PREFIX = _Setting(
    "OFFICE_NETWORK_IPV6_WIDEST_PREFIX", default=48, lowest=48, highest=128
)


def read_office_network_limits(environ: Mapping[str, str]) -> OfficeNetworkLimits:
    """Return the Office Network limits set in `environ`, or the defaults.

    `OFFICE_NETWORKS_MAX` is 1 to 100 (default 20); the widest prefixes are
    `OFFICE_NETWORK_IPV4_WIDEST_PREFIX` (24 to 32, default 24) and
    `OFFICE_NETWORK_IPV6_WIDEST_PREFIX` (48 to 128, default 48).

    Raises:
        ImproperlyConfigured: A value isn't a whole number in its range; the setting is named,
            the value never repeated.
    """
    return OfficeNetworkLimits(
        max_networks=MAX_NETWORKS.read(environ),
        ipv4_widest_prefix=IPV4_WIDEST_PREFIX.read(environ),
        ipv6_widest_prefix=IPV6_WIDEST_PREFIX.read(environ),
    )
