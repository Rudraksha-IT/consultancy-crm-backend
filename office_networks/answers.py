"""Office Networks as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from ipaddress import ip_network

from config.office_networks import OfficeNetworkLimits
from office_networks.models import OfficeNetwork


def shown_network(stored: str) -> str:
    """The network as people write it: one address without "/32" or "/128", else the range."""
    network = ip_network(stored)
    if network.prefixlen == network.max_prefixlen:
        return str(network.network_address)
    return str(network)


def office_network_answer(office_network: OfficeNetwork) -> dict[str, object]:
    """One Office Network: its name and address or range."""
    return {
        "id": office_network.public_id,
        "label": office_network.label,
        "network": shown_network(office_network.network),
    }


def limits_answer(limits: OfficeNetworkLimits) -> dict[str, object]:
    """The limits the screen explains: how many networks and how wide a range may be."""
    return {
        "max_networks": limits.max_networks,
        "ipv4_widest_prefix": limits.ipv4_widest_prefix,
        "ipv6_widest_prefix": limits.ipv6_widest_prefix,
    }
