"""Office Networks, and the record of every one added or removed (spec 105, issue 55).

An Office Network is the office's own public internet address or a small range of them. It is
never a Staff member's address: client addresses are only compared with these (ADR 0004).
"""

from __future__ import annotations

from typing import Any

from django.db import models
from django.utils import timezone

from office_networks.exceptions import OfficeNetworkChangeIsAppendOnlyError
from office_networks.networks import MAX_NETWORK_LENGTH
from staff.models import STAFF_NAME_MAX_LENGTH, Staff

LABEL_MAX_LENGTH = 60


class OfficeNetwork(models.Model):
    """One internet address or range Staff may clock in from, with a name such as "Main wifi"."""

    label = models.CharField(max_length=LABEL_MAX_LENGTH)
    # The network in CIDR form as `networks.parse_office_network` returns it, e.g.
    # "110.44.115.82/32" or "2400:1a00:b030::/48": one spelling per network.
    network = models.CharField(max_length=MAX_NETWORK_LENGTH, unique=True)
    added_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("added_at", "pk")

    def __str__(self) -> str:
        return self.label


class OfficeNetworkAction(models.TextChoices):
    """What was done to an Office Network."""

    ADDED = "added", "Added"
    REMOVED = "removed", "Removed"


class AppendOnlyQuerySet(models.QuerySet["OfficeNetworkChange"]):
    """Refuses bulk changes and bulk deletes."""

    def update(self, **kwargs: Any) -> int:  # noqa: ANN401  # Django's own signature
        """Refuse: a change record is never changed."""
        raise OfficeNetworkChangeIsAppendOnlyError

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse: a change record is never deleted."""
        raise OfficeNetworkChangeIsAppendOnlyError


class OfficeNetworkChange(models.Model):
    """One Office Network added or removed: who, when and what, kept as it was."""

    action = models.CharField(max_length=10, choices=OfficeNetworkAction.choices)
    label = models.CharField(max_length=LABEL_MAX_LENGTH)
    network = models.CharField(max_length=MAX_NETWORK_LENGTH)
    at = models.DateTimeField(default=timezone.now)
    # Who did it. The name is kept as it was; a Platform Admin is never linked as Staff.
    by_staff = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    by_platform_admin = models.BooleanField(default=False)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ("-at", "-pk")

    def __str__(self) -> str:
        return f"{self.action} {self.label}"

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # Django's own signature
        """Add a new record; refuse to change one already stored."""
        if self.pk is not None:
            raise OfficeNetworkChangeIsAppendOnlyError
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:  # noqa: ANN401
        """Refuse: a change record is never deleted."""
        raise OfficeNetworkChangeIsAppendOnlyError
