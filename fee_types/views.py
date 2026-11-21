"""REST endpoints for Fee Types (spec 67-70).

Anyone who takes Payments reads the list (to pick one); only an Admin changes it, in
Configuration. Neither needs the Accounts Module: Payments are recorded without it.
"""

from __future__ import annotations

from collections.abc import Sequence

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from fee_types.answers import fee_type_answer
from fee_types.exceptions import (
    DuplicateFeeTypeError,
    FeeTypeInUseError,
    FeeTypeOrderError,
    OtherFeeTypeIsFixedError,
    TooManyFeeTypesError,
)
from fee_types.models import FeeType
from fee_types.serializers import (
    ORDER_MESSAGE,
    FeeTypeChangeSerializer,
    FeeTypeOrderSerializer,
    NewFeeTypeSerializer,
)
from fee_types.services import (
    MAX_FEE_TYPES,
    add_fee_type,
    change_fee_type,
    delete_fee_type,
    fee_types_in_order,
    fee_types_with_uses,
    reorder_fee_types,
)
from staff.context import get_request_staff
from staff.models import Role
from staff.permissions import IsAdmin
from students.permissions import CanRecordPayments

DUPLICATE_MESSAGE = "There is already a Fee Type with this name."
TOO_MANY_MESSAGE = f"An office can have at most {MAX_FEE_TYPES} Fee Types."
IN_USE_MESSAGE = "Fees or Payments use this Fee Type. Hide it instead."
OTHER_FIXED_MESSAGE = "Other is always there. It can't be renamed, hidden or deleted."


def _refused(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _answer(fee_type_id: int) -> dict[str, object]:
    return fee_type_answer(fee_types_with_uses().get(pk=fee_type_id), with_uses=True)


class FeeTypeListView(APIView):
    """Every Fee Type in order (GET), or a new one (POST, Admin only)."""

    parser_classes = (JSONParser,)

    def get_permissions(self) -> Sequence[BasePermission]:
        """Reading is for anyone who takes Payments; adding is for Admins."""
        if self.request.method == "GET":
            return (IsAuthenticated(), CanRecordPayments())
        return (IsAuthenticated(), IsAdmin())

    def get(self, request: Request) -> Response:
        """Return every Fee Type, hidden ones too; with their uses for an Admin."""
        is_admin = get_request_staff(request).role == Role.ADMIN
        found = fee_types_with_uses() if is_admin else fee_types_in_order()
        return Response({"results": [fee_type_answer(t, with_uses=is_admin) for t in found]})

    def post(self, request: Request) -> Response:
        """Add a Fee Type at the end and return it."""
        serializer = NewFeeTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            added = add_fee_type(serializer.name_value(), by=get_request_staff(request))
        except DuplicateFeeTypeError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"name": [DUPLICATE_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)
        except TooManyFeeTypesError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(TOO_MANY_MESSAGE)
        return Response(_answer(added.pk), status=status.HTTP_201_CREATED)


class FeeTypeView(APIView):
    """Rename, hide or show one Fee Type (PATCH), or delete an unused one (DELETE). Admins."""

    permission_classes = (IsAuthenticated, IsAdmin)
    parser_classes = (JSONParser,)

    def patch(self, request: Request, type_id: int) -> Response:
        """Change it and return it as stored."""
        serializer = FeeTypeChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            changed = change_fee_type(type_id, serializer.change(), by=get_request_staff(request))
        except FeeType.DoesNotExist as error:
            raise NotFound from error
        except OtherFeeTypeIsFixedError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(OTHER_FIXED_MESSAGE)
        except DuplicateFeeTypeError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"name": [DUPLICATE_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_answer(changed.pk))

    def delete(self, request: Request, type_id: int) -> Response:
        """Delete it if no Fee or Payment uses it."""
        try:
            delete_fee_type(type_id, by=get_request_staff(request))
        except FeeType.DoesNotExist as error:
            raise NotFound from error
        except OtherFeeTypeIsFixedError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(OTHER_FIXED_MESSAGE)
        except FeeTypeInUseError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(IN_USE_MESSAGE)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FeeTypeOrderView(APIView):
    """Put every Fee Type in a new order (PUT, Admins); the order is used in every list."""

    permission_classes = (IsAuthenticated, IsAdmin)
    parser_classes = (JSONParser,)

    def put(self, request: Request) -> Response:
        """Reorder and return every Fee Type in the new order."""
        serializer = FeeTypeOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reorder_fee_types(serializer.order_value(), by=get_request_staff(request))
        except FeeTypeOrderError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"order": [ORDER_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"results": [fee_type_answer(t, with_uses=True) for t in fee_types_with_uses()]}
        )
