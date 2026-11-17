"""Which internet addresses can be an Office Network, and matching an address against them."""

from __future__ import annotations

from ipaddress import IPv4Address, ip_address, ip_network

import pytest

from config.office_networks import OfficeNetworkLimits
from office_networks.networks import (
    NetworkProblem,
    OfficeNetworkRefusedError,
    is_on_office_network,
    overlaps_any,
    parse_office_network,
)

LIMITS = OfficeNetworkLimits(max_networks=20, ipv4_widest_prefix=24, ipv6_widest_prefix=48)


def problem_with(raw: str, limits: OfficeNetworkLimits = LIMITS) -> NetworkProblem:
    """Why `raw` is refused, failing if it isn't."""
    with pytest.raises(OfficeNetworkRefusedError) as raised:
        parse_office_network(raw, limits)
    return raised.value.problem


class TestParseOfficeNetwork:
    @pytest.mark.parametrize(
        ("raw", "stored"),
        [
            ("110.44.115.82", "110.44.115.82/32"),
            ("110.44.115.82/32", "110.44.115.82/32"),
            ("110.44.115.0/24", "110.44.115.0/24"),
            ("110.44.115.0/28", "110.44.115.0/28"),
            # Host bits in a range are dropped: it is the same network.
            ("110.44.115.77/24", "110.44.115.0/24"),
            ("2400:1a00:b030::1", "2400:1a00:b030::1/128"),
            ("2400:1a00:b030::/48", "2400:1a00:b030::/48"),
            ("2400:1A00:B030:9::/64", "2400:1a00:b030:9::/64"),
            # IPv4 written as IPv6 is stored as IPv4, as client_address() reads it.
            ("::ffff:110.44.115.82", "110.44.115.82/32"),
        ],
    )
    def test_public_addresses_and_narrow_ranges_are_taken(self, raw: str, stored: str) -> None:
        assert str(parse_office_network(raw, LIMITS)) == stored

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "office wifi",
            "110.44.115",
            "110.44.115.256",
            "110.44.115.82/",
            "110.44.115.82/33",
            "110.44.115.82/24/1",
            "110.44.115.82 ",
            " 110.44.115.82",
            "110.44.115.82,27.34.20.9",
            "110.44.115.0-110.44.115.9",
            "2400:1a00::b030::1",
            "fe80::1%eth0",
            "2400:1a00:b030::/129",
            "<script>alert(1)</script>",
            "'; DROP TABLE office_networks_officenetwork; --",
            "1" * 60,
        ],
    )
    def test_malformed_values_are_refused(self, raw: str) -> None:
        assert problem_with(raw) is NetworkProblem.MALFORMED

    @pytest.mark.parametrize(
        "raw", ["110.44.0.0/16", "110.44.114.0/23", "0.0.0.0/0", "2400:1a00::/32", "::/0"]
    )
    def test_ranges_wider_than_ipv4_24_or_ipv6_48_are_refused(self, raw: str) -> None:
        assert problem_with(raw) is NetworkProblem.TOO_WIDE

    def test_a_narrower_limit_from_the_settings_is_used(self) -> None:
        narrow = OfficeNetworkLimits(max_networks=20, ipv4_widest_prefix=28, ipv6_widest_prefix=64)

        assert problem_with("110.44.115.0/27", narrow) is NetworkProblem.TOO_WIDE
        assert problem_with("2400:1a00:b030::/63", narrow) is NetworkProblem.TOO_WIDE
        assert str(parse_office_network("110.44.115.0/28", narrow)) == "110.44.115.0/28"

    @pytest.mark.parametrize(
        "raw",
        [
            # Private
            "10.0.0.1",
            "172.16.5.4",
            "192.168.1.0/24",
            "fd12:3456:789a::1",
            # Shared (carrier-grade NAT)
            "100.64.0.1",
            # Loopback
            "127.0.0.1",
            "::1",
            # Link-local
            "169.254.10.1",
            "fe80::1",
            # Multicast
            "224.0.0.1",
            "233.252.0.1",
            "ff02::1",
            "ff0e::1",
            # Reserved, unspecified and documentation
            "240.0.0.1",
            "255.255.255.255",
            str(IPv4Address(0)),
            "::",
            "192.0.2.10",
            "198.51.100.0/24",
            "2001:db8::1",
            "::ffff:10.0.0.1",
        ],
    )
    def test_private_loopback_link_local_multicast_and_reserved_are_refused(self, raw: str) -> None:
        assert problem_with(raw) is NetworkProblem.NOT_PUBLIC


class TestOverlapsAny:
    def test_the_same_address_overlaps(self) -> None:
        existing = [ip_network("110.44.115.82/32")]

        assert overlaps_any(ip_network("110.44.115.82/32"), existing)

    def test_an_address_inside_a_listed_range_overlaps(self) -> None:
        existing = [ip_network("110.44.115.0/24")]

        assert overlaps_any(ip_network("110.44.115.9/32"), existing)

    def test_a_range_holding_a_listed_address_overlaps(self) -> None:
        existing = [ip_network("110.44.115.82/32")]

        assert overlaps_any(ip_network("110.44.115.0/24"), existing)

    def test_separate_networks_and_other_versions_do_not(self) -> None:
        existing = [ip_network("110.44.115.0/24"), ip_network("2400:1a00:b030::/48")]

        assert not overlaps_any(ip_network("110.44.116.0/24"), existing)
        assert not overlaps_any(ip_network("2400:1a00:b031::/48"), existing)
        assert not overlaps_any(ip_network("110.44.116.1/32"), [])


class TestIsOnOfficeNetwork:
    NETWORKS = (
        ip_network("110.44.115.82/32"),
        ip_network("27.34.20.0/24"),
        ip_network("2400:1a00:b030::/48"),
    )

    @pytest.mark.parametrize(
        "address", ["110.44.115.82", "27.34.20.0", "27.34.20.255", "2400:1a00:b030:ffff::1"]
    )
    def test_an_address_in_a_network_is_on_it(self, address: str) -> None:
        assert is_on_office_network(ip_address(address), self.NETWORKS)

    @pytest.mark.parametrize(
        "address", ["110.44.115.83", "27.34.21.0", "2400:1a00:b031::1", "::ffff:110.44.115.82"]
    )
    def test_an_address_outside_every_network_is_not(self, address: str) -> None:
        # The mapped IPv4 form never reaches here: client_address() gives it as IPv4.
        assert not is_on_office_network(ip_address(address), self.NETWORKS)

    def test_no_address_or_no_networks_is_never_on_one(self) -> None:
        assert not is_on_office_network(None, self.NETWORKS)
        assert not is_on_office_network(ip_address("110.44.115.82"), ())
