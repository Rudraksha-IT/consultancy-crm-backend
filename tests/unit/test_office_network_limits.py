"""The Office Network limits come from the environment, and can only be kept safe (ADR 0004)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.office_networks import OfficeNetworkLimits, read_office_network_limits


class TestReadOfficeNetworkLimits:
    def test_defaults_are_20_networks_ipv4_24_and_ipv6_48(self) -> None:
        assert read_office_network_limits({}) == OfficeNetworkLimits(
            max_networks=20, ipv4_widest_prefix=24, ipv6_widest_prefix=48
        )

    def test_each_can_be_set(self) -> None:
        environ = {
            "OFFICE_NETWORKS_MAX": "5",
            "OFFICE_NETWORK_IPV4_WIDEST_PREFIX": "28",
            "OFFICE_NETWORK_IPV6_WIDEST_PREFIX": "64",
        }

        assert read_office_network_limits(environ) == OfficeNetworkLimits(
            max_networks=5, ipv4_widest_prefix=28, ipv6_widest_prefix=64
        )

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("OFFICE_NETWORKS_MAX", "1"),
            ("OFFICE_NETWORKS_MAX", "100"),
            ("OFFICE_NETWORK_IPV4_WIDEST_PREFIX", "24"),
            ("OFFICE_NETWORK_IPV4_WIDEST_PREFIX", "32"),
            ("OFFICE_NETWORK_IPV6_WIDEST_PREFIX", "48"),
            ("OFFICE_NETWORK_IPV6_WIDEST_PREFIX", "128"),
        ],
    )
    def test_the_edges_are_allowed(self, name: str, value: str) -> None:
        read_office_network_limits({name: value})

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("OFFICE_NETWORKS_MAX", "0"),
            ("OFFICE_NETWORKS_MAX", "101"),
            # Ranges can only be made narrower than ADR 0004 allows, never wider.
            ("OFFICE_NETWORK_IPV4_WIDEST_PREFIX", "23"),
            ("OFFICE_NETWORK_IPV4_WIDEST_PREFIX", "33"),
            ("OFFICE_NETWORK_IPV6_WIDEST_PREFIX", "47"),
            ("OFFICE_NETWORK_IPV6_WIDEST_PREFIX", "129"),
            ("OFFICE_NETWORKS_MAX", ""),
            ("OFFICE_NETWORKS_MAX", "-1"),
            ("OFFICE_NETWORKS_MAX", "1.5"),
            ("OFFICE_NETWORKS_MAX", " 5"),
            ("OFFICE_NETWORK_IPV4_WIDEST_PREFIX", "twenty"),
            ("OFFICE_NETWORK_IPV6_WIDEST_PREFIX", "<script>"),
        ],
    )
    def test_refuses_anything_else_naming_the_setting_not_the_value(
        self, name: str, value: str
    ) -> None:
        with pytest.raises(ImproperlyConfigured) as raised:
            read_office_network_limits({name: value})
        with pytest.raises(ImproperlyConfigured) as for_any_value:
            read_office_network_limits({name: "x"})

        assert name in str(raised.value)
        # The same fixed message whatever was set, so the value is never repeated back.
        assert str(raised.value) == str(for_any_value.value)
