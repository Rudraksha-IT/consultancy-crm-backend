"""Which code runs each kind of Job. Each app registers its kinds in its AppConfig.ready()."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from jobs.exceptions import UnknownJobKindError

if TYPE_CHECKING:
    from jobs.models import Job


class JobHandler(Protocol):
    """Runs one kind of Job, and cleans up when the queue gives up on one."""

    # Stored on a failed Job and shown on the Platform: short, safe, never a traceback.
    failure_message: str

    def run(self, job: Job) -> None:
        """Do the work. Safe to run twice: each step checks what is already done."""

    def gave_up(self, job: Job) -> None:
        """Leave nothing half-made after the job failed, or got stuck too many times."""

    def log_context(self, job: Job) -> str:
        """Ids that name what the job works on in its log lines, e.g. `consultancy_id=5`."""

    def tenant_host(self, job: Job) -> str | None:
        """The Consultancy host its log lines carry as `tenant`, or None."""


_handlers: dict[str, JobHandler] = {}


def register_job_kind(kind: str, handler: JobHandler) -> None:
    """Make `handler` the one that runs Jobs of `kind` (registering again replaces it)."""
    _handlers[kind] = handler


def unregister_job_kind(kind: str) -> None:
    """Forget `kind` (for tests that register a kind of their own)."""
    _handlers.pop(kind, None)


def handler_for(kind: str) -> JobHandler:
    """Return the handler of `kind`.

    Raises:
        UnknownJobKindError: Nothing is registered for it.
    """
    try:
        return _handlers[kind]
    except KeyError:
        raise UnknownJobKindError(kind) from None
