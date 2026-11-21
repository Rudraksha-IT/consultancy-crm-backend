"""Test helpers for Support Access (issue 27): a Platform Admin opening a Consultancy."""

from __future__ import annotations

from dataclasses import dataclass

from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from platform_admin.models import PlatformAdmin, SupportVisit
from tests.conftest import ConsultancyContext, PlatformContext, use_access_token

ENTER_URL = "/api/support-access/enter/"
LOGOUT_URL = "/api/auth/logout/"


def start_url(consultancy_id: object) -> str:
    """The Platform endpoint that opens a Consultancy as Platform Admin."""
    return f"/api/platform/consultancies/{consultancy_id}/support-visits/"


@dataclass(frozen=True)
class Visit:
    """A Platform Admin inside an office: their client on its address, and the visit's tokens."""

    # The visit's database number, for checks on the stored row (the answer names it by UUID).
    visit_id: int
    client: APIClient
    access: str
    refresh: str


def start(platform_client: APIClient, office: ConsultancyContext, reason: str) -> dict[str, object]:
    """Start a visit from the Platform and return the answer (visit, host, entry code)."""
    response = platform_client.post(
        start_url(office.consultancy.public_id), {"reason": reason}, format="json"
    )
    assert response.status_code == 201, response.content
    answer: dict[str, object] = response.json()
    return answer


def enter(office: ConsultancyContext, code: str) -> APIClient:
    """Swap the entry code on the office's address; return the client (not yet logged in)."""
    client = office.new_client()
    client.post(ENTER_URL, {"code": code}, format="json")
    return client


def open_as_platform_admin(
    platform: PlatformContext,
    office: ConsultancyContext,
    admin: PlatformAdmin | None = None,
    reason: str = "Admin asked for help with fees",
) -> Visit:
    """Open `office` as a Platform Admin (a new one unless given) and return the visit."""
    admin = admin or platform.add_admin()
    platform_client = platform.new_client()
    use_access_token(platform_client, platform.tokens_for(admin).access)
    started = start(platform_client, office, reason)
    client = office.new_client()
    response = client.post(ENTER_URL, {"code": started["entry_code"]}, format="json")
    assert response.status_code == 200, response.content
    body = response.json()
    use_access_token(client, body["access"])
    visit = started["visit"]
    assert isinstance(visit, dict)
    with schema_context(get_public_schema_name()):
        visit_id = SupportVisit.objects.get(public_id=visit["id"]).pk
    return Visit(visit_id=visit_id, client=client, access=body["access"], refresh=body["refresh"])
