"""Only Platform Admins pass the Platform permission check, never Staff."""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from platform_admin.models import PlatformAdmin
from platform_admin.permissions import IsPlatformAdmin
from staff.models import Role, Staff


def request_from(user: AbstractBaseUser | AnonymousUser) -> Request:
    """Return a DRF request made by the given user."""
    request = Request(APIRequestFactory().get("/"))
    # The stubs type users as AUTH_USER_MODEL (Staff); Platform Admins are a second model.
    request.user = user  # type: ignore[assignment]
    return request


class TestIsPlatformAdmin:
    def test_allows_a_platform_admin(self) -> None:
        request = request_from(PlatformAdmin(name="Ops", email="ops@platform.test"))

        assert IsPlatformAdmin().has_permission(request, APIView())

    def test_refuses_an_authenticated_staff_member(self) -> None:
        request = request_from(Staff(name="Sita", email="sita@abc.test", role=Role.ADMIN))

        assert not IsPlatformAdmin().has_permission(request, APIView())

    def test_refuses_anonymous_users(self) -> None:
        assert not IsPlatformAdmin().has_permission(request_from(AnonymousUser()), APIView())
