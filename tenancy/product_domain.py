"""Moving every office's address to a new product domain (`change_product_domain`).

Only the domain part of each office host changes (`acme.old.example` becomes
`acme.new.example`); the subdomain, the schema and every record stay. The Platform and
Central login hosts are never stored: they follow `PRODUCT_DOMAIN` by themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction
from django_tenants.utils import get_public_schema_name, schema_context

from tenancy.models import Domain

# A host name is at most 253 characters (the Domain column's own limit).
MAX_HOST_LENGTH = 253
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class ProductDomainError(Exception):
    """Why the move can't be made; nothing was changed."""


@dataclass(frozen=True)
class Move:
    """One office host and the host it becomes."""

    old_host: str
    new_host: str


def clean_domain(value: str, option: str) -> str:
    """Return the domain in lowercase without a trailing dot, or refuse anything else."""
    domain = value.strip().lower().removesuffix(".")
    labels = domain.split(".")
    if (
        not domain
        or len(domain) > MAX_HOST_LENGTH
        or not all(_LABEL.fullmatch(label) for label in labels)
    ):
        message = f"{option} is not a domain name (letters, numbers, hyphens and dots only)."
        raise ProductDomainError(message)
    return domain


def plan_moves(old_domain: str, new_domain: str) -> list[Move]:
    """Return the moves for every office host under `old_domain`, or refuse.

    When `new_domain` sits inside `old_domain`, a host already under it stays as it is.
    Refuses the same domain twice, no host to move, a new host that is too long, and a new host
    another row already has.
    """
    if old_domain == new_domain:
        message = f"The product domain is already {new_domain}."
        raise ProductDomainError(message)
    with schema_context(get_public_schema_name()):
        rows = list(Domain.objects.values_list("domain", flat=True))
    # Moving into a subdomain of the old domain: hosts already there are not moved again.
    is_new_inside_old = new_domain.endswith(f".{old_domain}")
    moving = sorted(
        str(host)
        for host in rows
        if str(host).endswith(f".{old_domain}")
        and not (is_new_inside_old and str(host).endswith(f".{new_domain}"))
    )
    if not moving:
        message = f"No office address ends in .{old_domain}; nothing to move."
        raise ProductDomainError(message)
    moves = [Move(host, f"{host.removesuffix(f'.{old_domain}')}.{new_domain}") for host in moving]
    _check_new_hosts(moves, staying={str(host) for host in rows} - set(moving))
    return moves


def _check_new_hosts(moves: list[Move], staying: set[str]) -> None:
    """Refuse a new host that is too long or that a row which isn't moving already has."""
    for move in moves:
        if len(move.new_host) > MAX_HOST_LENGTH:
            message = f"{move.new_host} is too long (at most {MAX_HOST_LENGTH} characters)."
            raise ProductDomainError(message)
        if move.new_host in staying:
            message = f"{move.new_host} is already taken; nothing was moved."
            raise ProductDomainError(message)


def apply_moves(moves: list[Move]) -> None:
    """Make every move in one transaction: all of them or none.

    Each row first takes a placeholder host, so a new host that is another row's old host
    never clashes halfway; nobody sees the placeholders.
    """
    with schema_context(get_public_schema_name()), transaction.atomic():
        rows = {
            str(row.domain): row
            for row in Domain.objects.select_for_update().filter(
                domain__in=[move.old_host for move in moves]
            )
        }
        if len(rows) != len(moves):
            message = "The office addresses changed while moving; nothing was moved."
            raise ProductDomainError(message)
        for move in moves:
            row = rows[move.old_host]
            row.domain = f"moving-{row.pk}.invalid"
            row.save(update_fields=["domain"])
        for move in moves:
            row = rows[move.old_host]
            row.domain = move.new_host
            row.save(update_fields=["domain"])
