"""Which internet addresses can be an Office Network, and matching an address against them.

Pure functions, no database (ADR 0004 rule 5; spec "Client address and Office Networks").
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_network

from config.office_networks import OfficeNetworkLimits

type OfficeNetworkRange = IPv4Network | IPv6Network

# The longest IPv6 text (45 characters) plus "/128".
MAX_NETWORK_LENGTH = 49
# Only the characters an address or range is written with; anything else is refused before
# `ipaddress` sees it (it would take zones such as "%eth0", which an office never has).
NETWORK_TEXT = re.compile(r"^[0-9A-Fa-f:.]+(/[0-9]{1,3})?\Z")
_MAPPED_IPV4_PREFIX = 96
_IPV4 = 4


class NetworkProblem(StrEnum):
    """Why a value can't be an Office Network."""

    MALFORMED = "malformed"
    TOO_WIDE = "too_wide"
    NOT_PUBLIC = "not_public"


class OfficeNetworkRefusedError(ValueError):
    """The value can't be an Office Network; `problem` says why."""

    def __init__(self, problem: NetworkProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


def parse_office_network(raw: str, limits: OfficeNetworkLimits) -> OfficeNetworkRange:
    """Return `raw` (one address, or a range in CIDR form) as the network to store.

    A single address is a /32 (IPv4) or /128 (IPv6) network. Host bits in a range are dropped
    ("110.44.115.7/24" is 110.44.115.0/24). IPv4 written as IPv6 ("::ffff:a.b.c.d") is IPv4.

    Raises:
        OfficeNetworkRefusedError: It isn't an address or range, it is wider than `limits`
            allow, or it isn't a public internet address (private, shared, loopback,
            link-local, multicast, reserved or documentation).
    """
    network = _as_network(raw)
    widest = limits.ipv4_widest_prefix if network.version == _IPV4 else limits.ipv6_widest_prefix
    if network.prefixlen < widest:
        raise OfficeNetworkRefusedError(NetworkProblem.TOO_WIDE)
    if not _is_public(network):
        raise OfficeNetworkRefusedError(NetworkProblem.NOT_PUBLIC)
    return network


def overlaps_any(network: OfficeNetworkRange, existing: Iterable[OfficeNetworkRange]) -> bool:
    """Return whether `network` is, holds or sits inside one of `existing`."""
    return any(network.version == other.version and network.overlaps(other) for other in existing)


def is_on_office_network(
    address: IPv4Address | IPv6Address | None, networks: Iterable[OfficeNetworkRange]
) -> bool:
    """Return whether `address` is in one of `networks`; no address is never on one."""
    if address is None:
        return False
    return any(address.version == net.version and address in net for net in networks)


def _as_network(raw: str) -> OfficeNetworkRange:
    if len(raw) > MAX_NETWORK_LENGTH or not NETWORK_TEXT.match(raw):
        raise OfficeNetworkRefusedError(NetworkProblem.MALFORMED)
    try:
        network = ip_network(raw, strict=False)
    except ValueError as error:
        raise OfficeNetworkRefusedError(NetworkProblem.MALFORMED) from error
    mapped = network.network_address.ipv4_mapped if isinstance(network, IPv6Network) else None
    if mapped is not None and network.prefixlen >= _MAPPED_IPV4_PREFIX:
        return IPv4Network((mapped, network.prefixlen - _MAPPED_IPV4_PREFIX))
    return network


def _is_public(network: OfficeNetworkRange) -> bool:
    """Every address in it is one the internet (and so Cloudflare) can see."""
    if network.is_multicast:
        return False
    return network.network_address.is_global and network.broadcast_address.is_global
