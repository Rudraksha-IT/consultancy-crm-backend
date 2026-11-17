"""Adding and removing Office Networks, and asking whether an address is on one (issue 55).

Call inside the Consultancy's schema. Who may do it is the views' job, through `access.policy`.
"""

from __future__ import annotations

import logging
from ipaddress import ip_network

from django.conf import settings
from django.db import connection, transaction
from django.db.models import QuerySet

from config.office_networks import OfficeNetworkLimits
from office_networks.exceptions import OfficeNetworkOverlapsError, TooManyOfficeNetworksError
from office_networks.models import OfficeNetwork, OfficeNetworkAction, OfficeNetworkChange
from office_networks.networks import OfficeNetworkRange, is_on_office_network, overlaps_any
from staff.actors import actor_name, is_platform_visitor, stored_staff
from staff.models import Staff
from tenancy.client_address import ClientAddress

# Changes shown under the list on the Configuration page, newest first.
CHANGES_SHOWN = 50
# Adds in one Consultancy take this lock in turn, so two at once can't pass the limit or overlap.
_ADD_LOCK = "office_networks.add"

logger = logging.getLogger(__name__)


def office_network_limits() -> OfficeNetworkLimits:
    """The limits from the environment (`config.office_networks`)."""
    limits: OfficeNetworkLimits = settings.OFFICE_NETWORK_LIMITS
    return limits


def office_networks() -> QuerySet[OfficeNetwork]:
    """Every Office Network, oldest first."""
    return OfficeNetwork.objects.order_by("added_at", "pk")


def recent_changes() -> QuerySet[OfficeNetworkChange]:
    """The latest Office Networks added or removed, newest first."""
    return OfficeNetworkChange.objects.order_by("-at", "-pk")[:CHANGES_SHOWN]


def add_office_network(label: str, network: OfficeNetworkRange, by: Staff) -> OfficeNetwork:
    """Add an Office Network (already checked by `networks.parse_office_network`) and record it.

    Raises:
        TooManyOfficeNetworksError: The office already has the most it may have.
        OfficeNetworkOverlapsError: It is, holds or sits inside one already on the list.
    """
    with transaction.atomic():
        _lock_adds()
        stored = list(office_networks())
        if len(stored) >= office_network_limits().max_networks:
            raise TooManyOfficeNetworksError
        if overlaps_any(network, (ip_network(row.network) for row in stored)):
            raise OfficeNetworkOverlapsError
        added = OfficeNetwork.objects.create(label=label, network=str(network))
        _record(OfficeNetworkAction.ADDED, added, by)
    logger.info("Office network added: office_network_id=%s", added.pk)
    return added


def remove_office_network(office_network_id: int, by: Staff) -> None:
    """Remove an Office Network and record it.

    Raises:
        OfficeNetwork.DoesNotExist: There is no such Office Network here.
    """
    with transaction.atomic():
        removed = OfficeNetwork.objects.select_for_update().get(pk=office_network_id)
        _record(OfficeNetworkAction.REMOVED, removed, by)
        removed.delete()
    logger.info("Office network removed: office_network_id=%s", office_network_id)


def is_on_an_office_network(address: ClientAddress | None) -> bool:
    """Return whether `address` (from `tenancy.client_address`) is on one of the office's."""
    if address is None:
        return False
    return is_on_office_network(address, (ip_network(row.network) for row in office_networks()))


def _record(action: OfficeNetworkAction, office_network: OfficeNetwork, by: Staff) -> None:
    OfficeNetworkChange.objects.create(
        action=action,
        label=office_network.label,
        network=office_network.network,
        by_staff=stored_staff(by),
        by_name=actor_name(by),
        by_platform_admin=is_platform_visitor(by),
    )


def _lock_adds() -> None:
    """Take this Consultancy's add lock until the transaction ends (keyed by its schema)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(current_schema() || %s))", [_ADD_LOCK]
        )
