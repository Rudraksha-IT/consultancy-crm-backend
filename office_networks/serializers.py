"""What the Office Network endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from config.office_networks import OfficeNetworkLimits
from config.serializers import StrictCharField, StrictSerializer
from office_networks.models import LABEL_MAX_LENGTH
from office_networks.networks import (
    MAX_NETWORK_LENGTH,
    NetworkProblem,
    OfficeNetworkRange,
    OfficeNetworkRefusedError,
    parse_office_network,
)
from office_networks.services import office_network_limits
from staff.serializers import PlainTextField

LABEL_MESSAGE = "Give the network a name."
LABEL_LENGTH_MESSAGE = f"Use at most {LABEL_MAX_LENGTH} characters."
MALFORMED_MESSAGE = "That doesn't look like an internet address, for example 110.44.115.82."
NOT_PUBLIC_MESSAGE = (
    "That's a private or reserved address, which only works inside the office. "
    "Use the address the internet sees, shown above."
)
_ALL_MESSAGES = ("required", "null", "blank", "invalid")


def too_wide_message(limits: OfficeNetworkLimits, *, is_ipv6: bool) -> str:
    """The refusal for a range wider than the limits allow, naming the widest allowed."""
    if is_ipv6:
        return f"That range is too wide. Use /{limits.ipv6_widest_prefix} or narrower."
    return (
        "That range is too wide. Use one address, "
        f"or a range of /{limits.ipv4_widest_prefix} or narrower."
    )


def refusal_message(problem: NetworkProblem, limits: OfficeNetworkLimits, *, is_ipv6: bool) -> str:
    """What is wrong with a refused network, in plain words."""
    if problem is NetworkProblem.TOO_WIDE:
        return too_wide_message(limits, is_ipv6=is_ipv6)
    if problem is NetworkProblem.NOT_PUBLIC:
        return NOT_PUBLIC_MESSAGE
    return MALFORMED_MESSAGE


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


class LabelField(PlainTextField):
    """The network's name, e.g. "Main office wifi": one line, up to 60 characters."""

    def __init__(self) -> None:
        super().__init__(
            max_length=LABEL_MAX_LENGTH,
            error_messages={**_same_message(LABEL_MESSAGE), "max_length": LABEL_LENGTH_MESSAGE},
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse anything that isn't text with the same message as a missing name."""
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class NetworkField(StrictCharField):
    """One public internet address, or a range no wider than the limits allow (ADR 0004)."""

    def __init__(self) -> None:
        super().__init__(
            # Anything longer is refused as not an address, without saying how long it was.
            max_length=None,
            error_messages={**_same_message(MALFORMED_MESSAGE)},
        )

    def to_internal_value(self, data: object) -> OfficeNetworkRange:  # type: ignore[override]
        """Return the network to store, or refuse with what is wrong in plain words."""
        if not isinstance(data, str):
            self.fail("invalid")
        raw = super().to_internal_value(data)
        if len(raw) > MAX_NETWORK_LENGTH:
            self.fail("invalid")
        limits = office_network_limits()
        try:
            return parse_office_network(raw, limits)
        except OfficeNetworkRefusedError as refused:
            raise serializers.ValidationError(
                refusal_message(refused.problem, limits, is_ipv6=":" in raw)
            ) from refused


class NewOfficeNetworkSerializer(StrictSerializer[None]):
    """An Office Network to add: its name (`label`) and its address or range. Both are needed."""

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """`label` and `network`, declared here: a class attribute `label` would hide DRF's own."""
        return {"label": LabelField(), "network": NetworkField()}

    def label_text(self) -> str:
        """The checked name."""
        label: str = self.validated_data["label"]
        return label

    def office_network(self) -> OfficeNetworkRange:
        """The checked network, as it will be stored."""
        network: OfficeNetworkRange = self.validated_data["network"]
        return network
