"""Subdomains: how a Consultancy (or the Platform) is addressed under the product domain."""

from __future__ import annotations

import re

from django.conf import settings

# Only lowercase letters and numbers, starting with a letter (issue 64): a valid DNS label that is
# also, unchanged, a valid schema name (a schema name can't start with a number). Offices made
# earlier with a hyphen keep working; the rule applies to new ones.
SUBDOMAIN_PATTERN = re.compile(r"[a-z][a-z0-9]{0,62}")
# "www" is stripped from hosts by django-tenants; "public" is the shared schema; "api" is kept
# free for a future public API host.
# "admin", "platform" and "login" stay reserved even when PLATFORM_SUBDOMAIN or
# CENTRAL_LOGIN_SUBDOMAIN is set to something else, so no Consultancy can take an address that
# looks like ours.
RESERVED_SUBDOMAINS = frozenset({"www", "api", "public", "admin", "platform", "login"})
INVALID_SUBDOMAIN_MESSAGE = (
    "Use only lowercase letters and numbers, starting with a letter (at most 63)."
)
RESERVED_SUBDOMAIN_MESSAGE = "This subdomain is reserved."


def _is_reserved(subdomain: str) -> bool:
    return subdomain in RESERVED_SUBDOMAINS or subdomain in {
        settings.PLATFORM_SUBDOMAIN,
        settings.CENTRAL_LOGIN_SUBDOMAIN,
    }


def find_subdomain_problem(subdomain: str) -> str | None:
    """Return why a subdomain cannot be given to a new Consultancy, or None if it can.

    Does not check whether another Consultancy already uses it.
    """
    if not SUBDOMAIN_PATTERN.fullmatch(subdomain):
        return INVALID_SUBDOMAIN_MESSAGE
    if _is_reserved(subdomain):
        return RESERVED_SUBDOMAIN_MESSAGE
    return None


def schema_name_for(subdomain: str) -> str:
    """Return the PostgreSQL schema name for a subdomain (an older one's hyphens become _)."""
    return subdomain.replace("-", "_")


def build_host(subdomain: str) -> str:
    """Return the full host name for a subdomain of the product domain."""
    return f"{subdomain}.{settings.PRODUCT_DOMAIN}"


def subdomain_from_host(host: str) -> str:
    """Return the subdomain part of a host under the product domain."""
    return host.removesuffix(f".{settings.PRODUCT_DOMAIN}")


def get_platform_host() -> str:
    """Return the host Platform Admins use; it belongs to no Consultancy."""
    return build_host(settings.PLATFORM_SUBDOMAIN)


def get_central_login_host() -> str:
    """Return the Central login's host (issue 44); it belongs to no Consultancy."""
    return build_host(settings.CENTRAL_LOGIN_SUBDOMAIN)
