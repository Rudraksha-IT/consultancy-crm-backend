"""REST endpoints for notifications (spec 80). Always on; every Role has them."""

from __future__ import annotations

from django.core.paginator import EmptyPage
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.services import is_module_on
from modules.types import Module
from notifications.answers import notification_answer, page_answer
from notifications.models import Notification
from notifications.serializers import ReadAllSerializer, ReadSerializer
from notifications.services import (
    NOTIFICATIONS_PER_PAGE,
    notifications_for,
    read_all,
    set_read,
)
from staff.context import get_request_staff
from tasks.serializers import PageSerializer
from tenancy.context import get_request_consultancy


class NotificationListView(APIView):
    """My notifications, newest first, with how many are unread (GET)."""

    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        """Return one page of the logged-in Staff member's own notifications."""
        staff = get_request_staff(request)
        query = PageSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            found = notifications_for(
                staff,
                query.page_number(),
                timezone.localdate(),
                clock_in_on=is_module_on(get_request_consultancy(request), Module.CLOCK_IN),
            )
        except EmptyPage as error:
            raise NotFound from error
        return Response(page_answer(found, NOTIFICATIONS_PER_PAGE, staff))


class NotificationView(APIView):
    """One of my notifications: mark it read or unread (PATCH only)."""

    permission_classes = (IsAuthenticated,)
    parser_classes = (JSONParser,)

    def patch(self, request: Request, notification_id: int) -> Response:
        """Set whether it's read and return it. Someone else's is not found."""
        staff = get_request_staff(request)
        serializer = ReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            notification = set_read(staff, notification_id, read=serializer.is_read())
        except Notification.DoesNotExist as error:
            raise NotFound from error
        return Response(notification_answer(notification, staff))


class ReadAllView(APIView):
    """Mark all my notifications read (POST only)."""

    permission_classes = (IsAuthenticated,)
    parser_classes = (JSONParser,)

    def post(self, request: Request) -> Response:
        """Mark them and return the new unread count (always 0)."""
        staff = get_request_staff(request)
        ReadAllSerializer(data=request.data).is_valid(raise_exception=True)
        read_all(staff)
        return Response({"unread_count": 0})
