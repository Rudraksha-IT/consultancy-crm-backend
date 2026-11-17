"""Office Networks: the office's internet addresses Staff may clock in from (spec 104-105, 111).

Part of the Clock-in Module and for Admins only (issue 55, ADR 0004).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from django_tenants.utils import tenant_context
from rest_framework import status

from config.office_networks import OfficeNetworkLimits
from modules.services import switch_modules
from modules.types import Module
from office_networks.exceptions import OfficeNetworkChangeIsAppendOnlyError
from office_networks.models import OfficeNetwork, OfficeNetworkChange
from staff.models import Role
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_stage_and_counsellor_integration import INJECTION_STRINGS
from tests.support_access import open_as_platform_admin

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse
    from rest_framework.test import APIClient

LIST_URL = "/api/office-networks/"
CURRENT_URL = "/api/office-networks/current/"
ONLY_ADMIN = {"detail": "Only an Admin can manage the office network."}
LABEL_MESSAGE = "Give the network a name."
MALFORMED = "That doesn't look like an internet address, for example 110.44.115.82."
TOO_WIDE_V4 = "That range is too wide. Use one address, or a range of /24 or narrower."
TOO_WIDE_V6 = "That range is too wide. Use /48 or narrower."
NOT_PUBLIC = (
    "That's a private or reserved address, which only works inside the office. "
    "Use the address the internet sees, shown above."
)
ALREADY_ON_THE_LIST = "That network is already on the list."
OFFICE_ADDRESS = "110.44.115.82"
GOOD_BODY = {"label": "Main office wifi", "network": OFFICE_ADDRESS}
DEFAULT_LIMITS = {"max_networks": 20, "ipv4_widest_prefix": 24, "ipv6_widest_prefix": 48}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Clock-in on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.CLOCK_IN})
    return office


def network_url(pk: int) -> str:
    """One Office Network."""
    return f"{LIST_URL}{pk}/"


def add(client: APIClient, body: object) -> _MonkeyPatchedResponse:
    """POST a new Office Network."""
    return client.post(LIST_URL, body, format="json")


def stored(office: ConsultancyContext) -> list[tuple[str, str]]:
    """Every Office Network as stored: (label, network), oldest first."""
    with tenant_context(office.consultancy):
        return list(OfficeNetwork.objects.order_by("pk").values_list("label", "network"))


def changes(office: ConsultancyContext) -> list[tuple[str, str, str, str]]:
    """Every change as stored: (action, label, network, by), oldest first."""
    with tenant_context(office.consultancy):
        rows = OfficeNetworkChange.objects.order_by("pk")
        return list(rows.values_list("action", "label", "network", "by_name"))


def add_stored(office: ConsultancyContext, network: str, label: str = "Office") -> OfficeNetwork:
    """Store an Office Network directly."""
    with tenant_context(office.consultancy):
        return OfficeNetwork.objects.create(label=label, network=network)


class TestTheList:
    def test_starts_empty_with_the_limits(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"networks": [], "changes": [], "limits": DEFAULT_LIMITS}

    def test_an_admin_adds_one_and_sees_it_with_who_added_it(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, GOOD_BODY)

        assert response.status_code == status.HTTP_201_CREATED
        added = response.json()
        assert added == {"id": added["id"], "label": "Main office wifi", "network": OFFICE_ADDRESS}
        assert stored(abc) == [("Main office wifi", "110.44.115.82/32")]
        body = abc.client.get(LIST_URL).json()
        assert body["networks"] == [added]
        [change] = body["changes"]
        assert change == {
            "id": change["id"],
            "action": "added",
            "label": "Main office wifi",
            "network": OFFICE_ADDRESS,
            "by": "Test Admin",
            "at": change["at"],
        }

    @pytest.mark.parametrize(
        ("network", "shown", "kept"),
        [
            ("110.44.115.0/24", "110.44.115.0/24", "110.44.115.0/24"),
            ("110.44.115.77/24", "110.44.115.0/24", "110.44.115.0/24"),
            ("2400:1A00:B030::1", "2400:1a00:b030::1", "2400:1a00:b030::1/128"),
            ("2400:1a00:b030::/48", "2400:1a00:b030::/48", "2400:1a00:b030::/48"),
            ("::ffff:110.44.115.82", OFFICE_ADDRESS, "110.44.115.82/32"),
        ],
    )
    def test_ranges_and_ipv6_are_kept_in_one_spelling(
        self, abc: ConsultancyContext, network: str, shown: str, kept: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {"label": "Office", "network": network})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["network"] == shown
        assert stored(abc) == [("Office", kept)]

    def test_removing_one_is_recorded_under_the_list(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        added = add(abc.client, GOOD_BODY).json()

        response = abc.client.delete(network_url(added["id"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert stored(abc) == []
        body = abc.client.get(LIST_URL).json()
        assert body["networks"] == []
        assert [(c["action"], c["label"], c["network"]) for c in body["changes"]] == [
            ("removed", "Main office wifi", OFFICE_ADDRESS),
            ("added", "Main office wifi", OFFICE_ADDRESS),
        ]

    def test_removing_one_that_isnt_there_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.delete(network_url(999))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert changes(abc) == []

    def test_changes_are_append_only(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        add(abc.client, GOOD_BODY)

        with tenant_context(abc.consultancy):
            change = OfficeNetworkChange.objects.get()
            with pytest.raises(OfficeNetworkChangeIsAppendOnlyError):
                change.save()
            with pytest.raises(OfficeNetworkChangeIsAppendOnlyError):
                change.delete()
            with pytest.raises(OfficeNetworkChangeIsAppendOnlyError):
                OfficeNetworkChange.objects.all().delete()
            with pytest.raises(OfficeNetworkChangeIsAppendOnlyError):
                OfficeNetworkChange.objects.update(label="x")

    def test_a_platform_admin_adds_and_removes_under_their_own_name(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        visitor = open_as_platform_admin(platform_site, abc).client

        added = add(visitor, GOOD_BODY)
        removed = visitor.delete(network_url(added.json()["id"]))

        assert added.status_code == status.HTTP_201_CREATED
        assert removed.status_code == status.HTTP_204_NO_CONTENT
        with tenant_context(abc.consultancy):
            rows = OfficeNetworkChange.objects.order_by("pk")
            assert [(r.by_name, r.by_staff_id, r.by_platform_admin) for r in rows] == [
                ("Platform Ops (Platform Admin)", None, True),
                ("Platform Ops (Platform Admin)", None, True),
            ]


class TestRefused:
    @pytest.mark.parametrize(
        "network",
        [
            "",
            "office wifi",
            "110.44.115",
            "110.44.115.256",
            "110.44.115.82/33",
            "110.44.115.0-110.44.115.9",
            "fe80::1%eth0",
            "1" * 300,
            *INJECTION_STRINGS,
        ],
    )
    def test_malformed_addresses(self, abc: ConsultancyContext, network: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {"label": "Office", "network": network})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"network": [MALFORMED]}
        assert stored(abc) == []

    @pytest.mark.parametrize(
        ("network", "message"),
        [
            ("110.44.0.0/16", TOO_WIDE_V4),
            ("0.0.0.0/0", TOO_WIDE_V4),
            ("2400:1a00::/32", TOO_WIDE_V6),
        ],
    )
    def test_ranges_too_wide(self, abc: ConsultancyContext, network: str, message: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {"label": "Office", "network": network})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"network": [message]}

    @pytest.mark.parametrize(
        "network",
        [
            "192.168.1.10",
            "10.0.0.0/24",
            "127.0.0.1",
            "::1",
            "169.254.1.1",
            "fe80::1",
            "224.0.0.1",
            "ff02::1",
            "240.0.0.1",
            "100.64.0.1",
            "fd00::1",
            "2001:db8::1",
        ],
    )
    def test_private_loopback_link_local_multicast_and_reserved(
        self, abc: ConsultancyContext, network: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {"label": "Office", "network": network})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"network": [NOT_PUBLIC]}

    @pytest.mark.parametrize(
        ("listed", "new"),
        [
            ("110.44.115.82/32", "110.44.115.82"),
            ("110.44.115.0/24", "110.44.115.9"),
            ("110.44.115.82/32", "110.44.115.0/24"),
            ("2400:1a00:b030::/48", "2400:1a00:b030:1::/64"),
        ],
    )
    def test_duplicates_and_overlapping_ranges(
        self, abc: ConsultancyContext, listed: str, new: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        add_stored(abc, listed)

        response = add(abc.client, {"label": "Second", "network": new})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"network": [ALREADY_ON_THE_LIST]}
        assert stored(abc) == [("Office", listed)]
        assert changes(abc) == []

    def test_at_most_20_per_office(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        for last in range(20):
            add_stored(abc, f"110.44.115.{last}/32")

        response = add(abc.client, {"label": "One more", "network": "27.34.20.9"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "An office can have at most 20 networks."}
        assert len(stored(abc)) == 20

    @pytest.mark.parametrize(
        ("label", "message"),
        [
            ("", LABEL_MESSAGE),
            (None, LABEL_MESSAGE),
            (7, LABEL_MESSAGE),
            (["Office"], LABEL_MESSAGE),
            ("x" * 61, "Use at most 60 characters."),
            ("Main\nwifi", "Use letters, numbers and punctuation only."),
        ],
    )
    def test_bad_names(self, abc: ConsultancyContext, label: object, message: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {"label": label, "network": OFFICE_ADDRESS})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"label": [message]}

    @pytest.mark.parametrize("network", [None, 110, ["110.44.115.82"], {"a": 1}, True])
    def test_a_network_that_isnt_text(self, abc: ConsultancyContext, network: object) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {"label": "Office", "network": network})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"network": [MALFORMED]}

    def test_missing_fields(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"label": [LABEL_MESSAGE], "network": [MALFORMED]}

    def test_unknown_fields_are_refused_and_never_stored(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {**GOOD_BODY, "added_at": "2020-01-01", "<b>x</b>": 1})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored(abc) == []

    @pytest.mark.parametrize("label", INJECTION_STRINGS)
    def test_injection_shaped_names_are_kept_as_plain_text(
        self, abc: ConsultancyContext, label: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)

        response = add(abc.client, {"label": label, "network": OFFICE_ADDRESS})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["label"] == label

    def test_only_the_methods_needed_are_allowed(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        pk = add_stored(abc, "110.44.115.82/32").pk

        assert abc.client.put(LIST_URL, GOOD_BODY, format="json").status_code == 405
        assert abc.client.delete(LIST_URL).status_code == 405
        assert abc.client.get(network_url(pk)).status_code == 405
        assert abc.client.patch(network_url(pk), GOOD_BODY, format="json").status_code == 405
        assert abc.client.post(CURRENT_URL, {}, format="json").status_code == 405

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(LIST_URL, GOOD_BODY)

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        assert stored(abc) == []


class TestLimitsFromTheEnvironment:
    NARROW = OfficeNetworkLimits(max_networks=1, ipv4_widest_prefix=28, ipv6_widest_prefix=64)

    def test_narrower_limits_are_used_and_shown(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        with override_settings(OFFICE_NETWORK_LIMITS=self.NARROW):
            listed = abc.client.get(LIST_URL).json()
            wide_v4 = add(abc.client, {"label": "Office", "network": "110.44.115.0/27"})
            wide_v6 = add(abc.client, {"label": "Office", "network": "2400:1a00:b030::/63"})
            first = add(abc.client, {"label": "Office", "network": "110.44.115.0/28"})
            second = add(abc.client, GOOD_BODY)

        assert listed["limits"] == {
            "max_networks": 1,
            "ipv4_widest_prefix": 28,
            "ipv6_widest_prefix": 64,
        }
        assert wide_v4.json() == {
            "network": ["That range is too wide. Use one address, or a range of /28 or narrower."]
        }
        assert wide_v6.json() == {"network": ["That range is too wide. Use /64 or narrower."]}
        assert first.status_code == status.HTTP_201_CREATED
        assert second.json() == {"detail": "An office can have at most 1 networks."}


class TestCurrentNetwork:
    def test_shows_my_address_and_that_it_isnt_on_the_list(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = OFFICE_ADDRESS

        response = abc.client.get(CURRENT_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"address": OFFICE_ADDRESS, "covered": False}
        assert response["Cache-Control"] == "no-store"

    @pytest.mark.parametrize("listed", ["110.44.115.82/32", "110.44.115.0/24"])
    def test_says_when_it_is_already_covered(self, abc: ConsultancyContext, listed: str) -> None:
        abc.log_in_as(Role.ADMIN)
        add_stored(abc, listed)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = OFFICE_ADDRESS

        assert abc.client.get(CURRENT_URL).json() == {"address": OFFICE_ADDRESS, "covered": True}

    def test_ipv6(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        add_stored(abc, "2400:1a00:b030::/48")
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "2400:1a00:b030:9::1"

        assert abc.client.get(CURRENT_URL).json() == {
            "address": "2400:1a00:b030:9::1",
            "covered": True,
        }

    @pytest.mark.parametrize("sent", [None, "", "not-an-address", "<script>", "1.2.3.4, 5.6.7.8"])
    def test_without_a_usable_address_nothing_is_shown(
        self, abc: ConsultancyContext, sent: str | None
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        add_stored(abc, "110.44.115.82/32")
        if sent is not None:
            abc.client.defaults["HTTP_X_CLIENT_IP"] = sent

        assert abc.client.get(CURRENT_URL).json() == {"address": None, "covered": False}

    def test_the_address_is_never_logged_or_stored(
        self, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        abc.client.defaults["HTTP_X_CLIENT_IP"] = "27.34.20.9"

        abc.client.get(CURRENT_URL)
        add(abc.client, GOOD_BODY)
        abc.client.get(LIST_URL)

        assert "27.34.20.9" not in json_logs.text()
        assert "27.34.20.9" not in str(stored(abc)) + str(changes(abc))


ENDPOINTS = [
    ("get", LIST_URL),
    ("post", LIST_URL),
    ("get", CURRENT_URL),
    ("delete", network_url(1)),
]


def call(client: APIClient, method: str, url: str) -> _MonkeyPatchedResponse:
    """Call one endpoint with a good body where it takes one."""
    if method == "post":
        return client.post(url, GOOD_BODY, format="json")
    response: _MonkeyPatchedResponse = getattr(client, method)(url)
    return response


class TestWhoMay:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    @pytest.mark.parametrize(("method", "url"), ENDPOINTS)
    def test_only_admins(self, abc: ConsultancyContext, role: Role, method: str, url: str) -> None:
        add_stored(abc, "110.44.115.82/32")
        abc.log_in_as(role)

        response = call(abc.client, method, url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN
        assert stored(abc) == [("Office", "110.44.115.82/32")]

    @pytest.mark.parametrize(("method", "url"), ENDPOINTS)
    def test_logged_out_is_refused(self, abc: ConsultancyContext, method: str, url: str) -> None:
        response = call(abc.new_client(), method, url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("role", list(Role))
    @pytest.mark.parametrize(("method", "url"), ENDPOINTS)
    def test_with_clock_in_off_nothing_is_there_for_anyone(
        self, make_consultancy: ConsultancyFactory, role: Role, method: str, url: str
    ) -> None:
        office = make_consultancy("XYZ Overseas", "xyzoverseas")
        add_stored(office, "110.44.115.82/32")
        office.log_in_as(role)

        response = call(office.client, method, url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored(office) == [("Office", "110.44.115.82/32")]

    def test_switching_clock_in_off_hides_them_and_on_again_brings_them_back(
        self, abc: ConsultancyContext
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        added = add(abc.client, GOOD_BODY).json()

        switch_modules(abc.consultancy, set())
        hidden = abc.client.get(LIST_URL)
        switch_modules(abc.consultancy, {Module.CLOCK_IN})

        assert hidden.status_code == status.HTTP_404_NOT_FOUND
        assert abc.client.get(LIST_URL).json()["networks"] == [added]
