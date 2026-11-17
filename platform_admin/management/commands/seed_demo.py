"""Load the Acme demo office on a fresh local stack (asked 2026-11-17).

The compose `seed` service runs this once the backend and the worker are up. It does nothing
unless SEED_DEMO_DATA is "true" (set only in sample.env.backend, never in production) and does
nothing if the Acme office is already there, so a restart never loads the demo twice. The demo
itself is `demo/acme_seed.py`: it creates the office through the Platform's own setup, waits
for the worker to build it, then adds Staff, Students and six months of their story.
"""

from __future__ import annotations

import logging
import os
import runpy
from typing import Any

from django.conf import settings
from django_tenants.utils import schema_context

from config.commands import LoggedCommand
from tenancy.models import Consultancy

PUBLIC_SCHEMA = "public"
SEED_VAR = "SEED_DEMO_DATA"
DEMO_SCHEMA = "acme"
SCRIPT = settings.BASE_DIR / "demo" / "acme_seed.py"

logger = logging.getLogger(__name__)


def run_script(path: str) -> None:
    """Run the demo script as `manage.py shell < script` would."""
    runpy.run_path(path, run_name="__main__")


def _is_on() -> bool:
    return os.environ.get(SEED_VAR, "").strip().lower() == "true"


def _demo_is_there() -> bool:
    with schema_context(PUBLIC_SCHEMA):
        is_there: bool = Consultancy.objects.filter(schema_name=DEMO_SCHEMA).exists()
    return is_there


class Command(LoggedCommand):
    """`manage.py seed_demo`: no options; reads SEED_DEMO_DATA only."""

    help = "Load the Acme demo office once, when SEED_DEMO_DATA is true (local development)."

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401  # Django's signature
        """Log exactly one outcome: off, already there, or loaded."""
        if not _is_on():
            logger.info("demo data off; skipped")
            return
        if _demo_is_there():
            logger.info("demo data already there; skipped")
            return
        run_script(str(SCRIPT))
        logger.info("demo data loaded")
