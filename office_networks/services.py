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
from office_networks.models import OfficeNetwork
from office_networks.networks import OfficeNetworkRange, is_on_office_network, overlaps_any
from tenancy.client_address import ClientAddress

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


def add_office_network(label: str, network: OfficeNetworkRange) -> OfficeNetwork:
    """Add an Office Network (already checked by `networks.parse_office_network`).

    Nothing about who added it is kept (ticket 02).

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
    logger.info("Office network added: office_network_id=%s", added.pk)
    return added


def remove_office_network(office_network_id: int) -> None:
    """Remove an Office Network. Nothing about it is kept afterwards (ticket 02).

    Raises:
        OfficeNetwork.DoesNotExist: There is no such Office Network here.
    """
    OfficeNetwork.objects.get(pk=office_network_id).delete()
    logger.info("Office network removed: office_network_id=%s", office_network_id)


def is_on_an_office_network(address: ClientAddress | None) -> bool:
    """Return whether `address` (from `tenancy.client_address`) is on one of the office's."""
    if address is None:
        return False
    return is_on_office_network(address, (ip_network(row.network) for row in office_networks()))


def _lock_adds() -> None:
    """Take this Consultancy's add lock until the transaction ends (keyed by its schema)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(current_schema() || %s))", [_ADD_LOCK]
        )
