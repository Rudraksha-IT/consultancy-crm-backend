"""Management commands that log like requests do (issue 58).

Each run gets a fresh correlation ID, so one search shows all of its lines; its start, end
(with the duration) and any failure (with the traceback) are logged. A command run from inside
other work (a background job migrating a new office, issue 60) keeps that work's ID instead.
"""

from __future__ import annotations

import logging
import uuid
from time import monotonic
from typing import Any

from django.core.management.base import BaseCommand

from config.correlation import get_correlation_id, reset_correlation_id, set_correlation_id

logger = logging.getLogger(__name__)
_MS_PER_SECOND = 1000


class LoggedCommand(BaseCommand):
    """A command whose every run is one correlation ID, with start, end and failure logged."""

    def execute(self, *args: Any, **options: Any) -> str | None:  # noqa: ANN401  # Django's
        """Run the command inside its own correlation ID."""
        name = type(self).__module__.rsplit(".", 1)[-1]
        token = set_correlation_id(get_correlation_id() or str(uuid.uuid4()))
        started = monotonic()
        try:
            logger.info("command started: name=%s", name)
            output: str | None = super().execute(*args, **options)
        except BaseException:
            logger.exception("command failed: name=%s", name)
            raise
        else:
            duration_ms = round((monotonic() - started) * _MS_PER_SECOND)
            logger.info("command finished: name=%s duration_ms=%s", name, duration_ms)
            return output
        finally:
            reset_correlation_id(token)
