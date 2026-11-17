"""Typed access to the Consultancy that django-tenants has activated."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol, cast

from django.db import connection
from django.http import HttpRequest
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context
from rest_framework.request import Request

from tenancy.exceptions import ConsultancyNotResolvedError
from tenancy.models import Consultancy, Domain

REQUEST_TENANT_ATTRIBUTE = "tenant"


class _TenantConnection(Protocol):
    """The schema-switching methods django-tenants adds to the database connection."""

    schema_name: str

    def set_schema_to_public(self) -> None: ...


def get_request_consultancy(request: HttpRequest | Request) -> Consultancy:
    """Return the Consultancy resolved from the request's host.

    Raises:
        ConsultancyNotResolvedError: The request was not routed to a Consultancy.
    """
    tenant = getattr(request, REQUEST_TENANT_ATTRIBUTE, None)
    if not isinstance(tenant, Consultancy):
        raise ConsultancyNotResolvedError
    return tenant


def activate_public_schema() -> None:
    """Point the database connection back at the shared public schema."""
    cast(_TenantConnection, connection).set_schema_to_public()


def get_active_schema_name() -> str:
    """Return the schema the database connection is on (a Consultancy's, or public)."""
    return cast(_TenantConnection, connection).schema_name


def is_public_schema_active() -> bool:
    """Return whether the database connection is on the shared public schema."""
    public_schema: str = get_public_schema_name()
    return get_active_schema_name() == public_schema


@contextmanager
def office_context(office: Consultancy) -> Iterator[None]:
    """Work inside `office`'s schema, its log lines carrying its host as `tenant`.

    For jobs that loop over Consultancies (a request gets its host from the middleware).
    """
    with schema_context(get_public_schema_name()):
        primary = Domain.objects.filter(tenant=office, is_primary=True).first()
    office.domain_url = primary.domain if primary is not None else office.schema_name
    with tenant_context(office):
        yield
