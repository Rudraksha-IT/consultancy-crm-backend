"""The `worker` container's command (issue 60): run queued Jobs one at a time, until stopped.

`python manage.py run_jobs` checks the queue every JOB_POLL_SECONDS. SIGTERM (a deploy or
`docker stop`) lets the current Job finish, then stops. `--until-empty` stops once the queue
is empty, for a one-off run.
"""

from __future__ import annotations

import logging
import signal
import threading
from argparse import ArgumentParser
from datetime import timedelta
from types import FrameType
from typing import Any

from django.conf import settings

from config.commands import LoggedCommand
from config.jobs import JobSettings
from jobs.services import run_next_job

_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)

logger = logging.getLogger(__name__)


class Command(LoggedCommand):
    """`manage.py run_jobs [--until-empty]`."""

    help = "Run queued jobs one at a time until stopped (SIGTERM finishes the current job)."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Add `--until-empty`."""
        parser.add_argument(
            "--until-empty", action="store_true", help="Stop once no job is waiting."
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401  # Django's signature
        """Run Jobs until a stop signal arrives (or the queue is empty, with --until-empty)."""
        job_settings: JobSettings = settings.JOB_SETTINGS
        stop = threading.Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            logger.info("worker stopping after the current job: signal=%s", signum)
            stop.set()

        previous = {sig: signal.signal(sig, request_stop) for sig in _STOP_SIGNALS}
        logger.info(
            "worker started: poll_seconds=%s timeout_seconds=%s",
            job_settings.poll_seconds,
            job_settings.timeout_seconds,
        )
        try:
            _run_until_stopped(job_settings, stop, until_empty=options["until_empty"])
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        logger.info("worker stopped")


def _run_until_stopped(
    job_settings: JobSettings, stop: threading.Event, *, until_empty: bool
) -> None:
    """Run Jobs one at a time, waiting between empty checks, until `stop` is set."""
    timeout = timedelta(seconds=job_settings.timeout_seconds)
    while not stop.is_set():
        if run_next_job(timeout):
            continue
        if until_empty:
            return
        stop.wait(job_settings.poll_seconds)
