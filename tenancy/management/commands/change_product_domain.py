"""Move every office's address to a new product domain.

`python manage.py change_product_domain --from old.example --to new.example` prints what would
change; add `--apply` to change it. Then set PRODUCT_DOMAIN (backend and frontend) and
DEFAULT_FROM_EMAIL to the new domain and restart both. Steps in the workspace's
`docs/deployment.md`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.core.management import CommandError

from config.commands import LoggedCommand
from tenancy.product_domain import ProductDomainError, apply_moves, clean_domain, plan_moves

if TYPE_CHECKING:
    from django.core.management.base import CommandParser

logger = logging.getLogger(__name__)


class Command(LoggedCommand):
    """Change the domain part of every office host, keeping each office's subdomain."""

    help = "Move every office's address from one product domain to another (dry run by default)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Take the old and new domains, and --apply to make the change."""
        parser.add_argument("--from", dest="old_domain", required=True, help="Current domain.")
        parser.add_argument("--to", dest="new_domain", required=True, help="New domain.")
        parser.add_argument(
            "--apply", action="store_true", help="Make the change (without it: a dry run)."
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401  # Django's signature
        """Print the moves; with --apply make them all in one step."""
        try:
            old_domain = clean_domain(str(options["old_domain"]), "--from")
            new_domain = clean_domain(str(options["new_domain"]), "--to")
            moves = plan_moves(old_domain, new_domain)
            for move in moves:
                self.stdout.write(f"{move.old_host} -> {move.new_host}")
            if not options["apply"]:
                self.stdout.write(
                    f"Dry run: {len(moves)} office addresses would move. "
                    "Run again with --apply to move them."
                )
                return
            apply_moves(moves)
        except ProductDomainError as error:
            raise CommandError(str(error)) from error
        logger.info("product domain changed: offices=%s", len(moves))
        self.stdout.write(
            f"Moved {len(moves)} office addresses to .{new_domain}.\n"
            f"Now set PRODUCT_DOMAIN={new_domain} (backend and frontend) and "
            f"DEFAULT_FROM_EMAIL=no-reply@{new_domain}, then restart them. Everyone logs in "
            "again once."
        )
