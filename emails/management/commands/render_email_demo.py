"""`manage.py render_email_demo`: write the email preview page (spec stories 94 to 97).

Renders every email kind with the made-up office "Himal Education" through the production
builder into one self-contained HTML file, which is then published as a private link.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from django.core.management.base import CommandParser

from config.commands import LoggedCommand
from emails.demo import demo_emails, render_demo_page

DEFAULT_OUTPUT = "email-demo.html"

logger = logging.getLogger(__name__)


class Command(LoggedCommand):
    """Write the preview page to `--output` (default `email-demo.html`)."""

    help = "Render every email with a made-up office into one preview page."

    def add_arguments(self, parser: CommandParser) -> None:
        """Where to write the page."""
        parser.add_argument("--output", default=DEFAULT_OUTPUT, help="The HTML file to write.")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401  # Django's signature
        """Render, write, and log how many emails the page holds."""
        emails = demo_emails()
        Path(options["output"]).write_text(render_demo_page(emails), encoding="utf-8")
        logger.info("email demo written: emails=%s", len(emails))
