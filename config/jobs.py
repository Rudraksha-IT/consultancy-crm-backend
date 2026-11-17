"""How the `worker` runs the job queue (issue 60), read from the environment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class JobSettings:
    """How often the worker checks the queue, and when a running job counts as stuck."""

    poll_seconds: int
    timeout_seconds: int


@dataclass(frozen=True)
class _Setting:
    name: str
    default: int
    lowest: int
    highest: int

    def read(self, environ: Mapping[str, str]) -> int:
        value = environ.get(self.name, str(self.default))
        if not value.isdigit() or not self.lowest <= int(value) <= self.highest:
            message = f"{self.name} must be a whole number from {self.lowest} to {self.highest}."
            raise ImproperlyConfigured(message)
        return int(value)


POLL_SECONDS = _Setting("JOB_POLL_SECONDS", default=5, lowest=1, highest=60)
TIMEOUT_SECONDS = _Setting("JOB_TIMEOUT_SECONDS", default=900, lowest=60, highest=86_400)


def read_job_settings(environ: Mapping[str, str]) -> JobSettings:
    """Return the worker's settings set in `environ`, or the defaults.

    `JOB_POLL_SECONDS` is 1 to 60 (default 5); `JOB_TIMEOUT_SECONDS` is 60 to 86400 (default 900).

    Raises:
        ImproperlyConfigured: A value is not a whole number in its range.
    """
    return JobSettings(
        poll_seconds=POLL_SECONDS.read(environ), timeout_seconds=TIMEOUT_SECONDS.read(environ)
    )
