"""Reading the resolved Consultancy off a request."""

from __future__ import annotations

import pytest
from django.http import HttpRequest

from tenancy.context import get_request_consultancy
from tenancy.exceptions import ConsultancyNotResolvedError
from tenancy.models import Consultancy


class TestGetRequestConsultancy:
    def test_returns_consultancy_attached_by_tenant_middleware(self) -> None:
        consultancy = Consultancy(name="ABC Education", schema_name="abcedu")
        request = HttpRequest()
        request.tenant = consultancy  # type: ignore[attr-defined]  # set by django-tenants

        result = get_request_consultancy(request)

        assert result is consultancy

    def test_raises_when_request_has_no_consultancy(self) -> None:
        request = HttpRequest()

        with pytest.raises(ConsultancyNotResolvedError):
            get_request_consultancy(request)
