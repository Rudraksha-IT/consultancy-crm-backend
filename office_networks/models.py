"""Office Networks (spec 105, issue 55). Adding and removing one records nothing (ticket 02).

An Office Network is the office's own public internet address or a small range of them. It is
never a Staff member's address: client addresses are only compared with these (ADR 0004).
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from config.public_ids import public_id_field
from office_networks.networks import MAX_NETWORK_LENGTH

LABEL_MAX_LENGTH = 60


class OfficeNetwork(models.Model):
    """One internet address or range Staff may clock in from, with a name such as "Main wifi"."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    label = models.CharField(max_length=LABEL_MAX_LENGTH)
    # The network in CIDR form as `networks.parse_office_network` returns it, e.g.
    # "110.44.115.82/32" or "2400:1a00:b030::/48": one spelling per network.
    network = models.CharField(max_length=MAX_NETWORK_LENGTH, unique=True)
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("added_at", "pk")

    def __str__(self) -> str:
        return self.label
