"""REST endpoints for Office Networks, part of the Clock-in Module, for Admins (spec 105).

Every endpoint answers 404 while Clock-in is off, for every Role, before any other check. A
Platform Admin in Support Access passes as an Admin (issue 27).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import ONLY_ADMIN_OFFICE_NETWORKS_MESSAGE, why_cannot_manage_office_networks
from modules.permissions import requires_module
from modules.types import Module
from office_networks.answers import (
    limits_answer,
    office_network_answer,
    shown_network,
)
from office_networks.exceptions import OfficeNetworkOverlapsError, TooManyOfficeNetworksError
from office_networks.models import OfficeNetwork
from office_networks.networks import suggested_network
from office_networks.serializers import NewOfficeNetworkSerializer
from office_networks.services import (
    add_office_network,
    is_on_an_office_network,
    office_network_limits,
    office_networks,
    remove_office_network,
)
from staff.models import Staff
from tenancy.client_address import client_address

ALREADY_ON_THE_LIST_MESSAGE = "That network is already on the list."
ClockInModuleIsOn = requires_module(Module.CLOCK_IN)


def too_many_message(max_networks: int) -> str:
    """The refusal when the office already has as many networks as it may."""
    return f"An office can have at most {max_networks} networks."


class CanManageOfficeNetworks(BasePermission):
    """Office Networks are for Admins (spec 105)."""

    message = ONLY_ADMIN_OFFICE_NETWORKS_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member see and change Office Networks."""
        user = request.user
        return isinstance(user, Staff) and why_cannot_manage_office_networks(user) is None


PERMISSIONS = (IsAuthenticated, ClockInModuleIsOn, CanManageOfficeNetworks)


class OfficeNetworksView(APIView):
    """The Office Networks and their limits (GET), or a new one (POST)."""

    permission_classes = PERMISSIONS
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return every Office Network and the limits; no adds and removes (ticket 02)."""
        return Response(
            {
                "networks": [office_network_answer(row) for row in office_networks()],
                "limits": limits_answer(office_network_limits()),
            }
        )

    def post(self, request: Request) -> Response:
        """Check and add an Office Network."""
        body = NewOfficeNetworkSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            added = add_office_network(body.label_text(), body.office_network())
        except OfficeNetworkOverlapsError:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"network": [ALREADY_ON_THE_LIST_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST
            )
        except TooManyOfficeNetworksError:
            # expected refusal, answered with its status; the request line logs it
            message = too_many_message(office_network_limits().max_networks)
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(office_network_answer(added), status=status.HTTP_201_CREATED)


class OfficeNetworkView(APIView):
    """One Office Network: remove it (DELETE)."""

    permission_classes = PERMISSIONS

    def delete(self, request: Request, office_network_id: int) -> Response:
        """Remove it."""
        try:
            remove_office_network(office_network_id)
        except OfficeNetwork.DoesNotExist as error:
            raise NotFound from error
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentNetworkView(APIView):
    """The caller's own internet address, what adding it would add, and whether it is covered.

    For IPv4 the suggestion is the address itself; for IPv6 its /64, the office's group, so every
    device on the office Wi-Fi can clock in (spec 1-2 of the first live fixes).

    The one place an address is ever given back (ADR 0004 rule 4): only to an Admin, only their
    own, so they can press "Add the network I'm on now". It is never stored or logged.
    """

    permission_classes = PERMISSIONS

    def get(self, request: Request) -> Response:
        """Return the address and suggestion (None when unknown) and whether it is covered."""
        address = client_address(request)
        suggested = suggested_network(address)
        return Response(
            {
                "address": None if address is None else str(address),
                "suggested_network": None if suggested is None else shown_network(str(suggested)),
                "covered": is_on_an_office_network(address),
            },
            headers={"Cache-Control": "no-store"},
        )
