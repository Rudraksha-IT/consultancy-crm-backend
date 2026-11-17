"""Reading the logged-in Staff member off a request."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest

from staff.context import get_request_staff
from staff.exceptions import StaffNotAuthenticatedError
from staff.models import Role, Staff


class TestGetRequestStaff:
    def test_returns_staff_attached_by_authentication_middleware(self) -> None:
        staff = Staff(name="Sita", email="sita@abcedu.com", role=Role.COUNSELLOR)
        request = HttpRequest()
        request.user = staff

        result = get_request_staff(request)

        assert result is staff

    def test_raises_when_nobody_is_logged_in(self) -> None:
        request = HttpRequest()
        request.user = AnonymousUser()

        with pytest.raises(StaffNotAuthenticatedError):
            get_request_staff(request)
