"""The job queue (issue 60): queue a Job, let one worker claim it, run it, recover stuck ones.

Call these on the public schema. A worker claims with `SELECT … FOR UPDATE SKIP LOCKED`, so
two workers can never take the same Job.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta
from time import monotonic

from django.db import transaction
from django.utils import timezone

from config.correlation import get_correlation_id, reset_correlation_id, set_correlation_id
from config.logging_utils import reset_log_tenant, set_log_tenant
from jobs.exceptions import UnsafeJobPayloadError
from jobs.models import JOB_ERROR_MAX_LENGTH, Job, JobState
from jobs.registry import handler_for
from platform_admin.models import PlatformAdmin
from tenancy.context import activate_public_schema

# Payload keys are snake_case words; none may name a secret.
_KEY = re.compile(r"[a-z][a-z0-9_]{0,49}")
_SECRET_WORDS = ("password", "token", "code", "secret", "cookie", "key")
PAYLOAD_VALUE_MAX_LENGTH = 500
STUCK_TOO_OFTEN_MESSAGE = "The job stopped before finishing too many times."
_MS_PER_SECOND = 1000

logger = logging.getLogger(__name__)

PayloadValue = str | int | bool | None


def queue_job(
    kind: str, payload: dict[str, PayloadValue], *, created_by: PlatformAdmin | None = None
) -> Job:
    """Add a Job for the worker, carrying the current request's correlation ID.

    Raises:
        UnsafeJobPayloadError: The payload holds more than ids and plain values, or a key
            that names a secret (a password, token or Code is never stored).
        UnknownJobKindError: No handler runs this kind.
    """
    handler_for(kind)
    _check_payload(payload)
    job = Job.objects.create(
        kind=kind,
        payload=payload,
        created_by=created_by,
        correlation_id=get_correlation_id() or "",
    )
    logger.info("job queued: job_id=%s kind=%s", job.pk, kind)
    return job


def _check_payload(payload: dict[str, PayloadValue]) -> None:
    for key, value in payload.items():
        if not _KEY.fullmatch(key) or any(word in key for word in _SECRET_WORDS):
            raise UnsafeJobPayloadError(key)
        if value is not None and not isinstance(value, str | int | bool):
            raise UnsafeJobPayloadError(key)
        if isinstance(value, str) and len(value) > PAYLOAD_VALUE_MAX_LENGTH:
            raise UnsafeJobPayloadError(key)


def claim_next_job(now: datetime | None = None) -> Job | None:
    """Take the oldest queued Job for this worker, or None when there is none to take.

    A Job another worker is claiming at the same moment is skipped, never waited for.
    """
    now = now or timezone.now()
    with transaction.atomic():
        job = (
            Job.objects.select_for_update(skip_locked=True)
            .filter(state=JobState.QUEUED)
            .order_by("pk")
            .first()
        )
        if job is None:
            return None
        job.state = JobState.RUNNING
        job.attempts += 1
        job.locked_at = now
        job.started_at = now
        job.save(update_fields=["state", "attempts", "locked_at", "started_at"])
    return job


def recover_stuck_jobs(timeout: timedelta, now: datetime | None = None) -> int:
    """Put back in the queue every Job running for longer than `timeout` (its worker died).

    One that has used all its attempts fails instead, and its handler cleans up.

    Returns:
        How many stuck Jobs were found.
    """
    now = now or timezone.now()
    with transaction.atomic():
        stuck = list(
            Job.objects.select_for_update(skip_locked=True).filter(
                state=JobState.RUNNING, locked_at__lt=now - timeout
            )
        )
        for job in stuck:
            if job.attempts < job.max_attempts:
                job.state = JobState.QUEUED
                job.locked_at = None
                job.save(update_fields=["state", "locked_at"])
                logger.warning(
                    "stuck job queued again: job_id=%s kind=%s attempts=%s",
                    job.pk,
                    job.kind,
                    job.attempts,
                )
    for job in stuck:
        if job.state == JobState.RUNNING:
            _give_up(job, STUCK_TOO_OFTEN_MESSAGE, now)
    return len(stuck)


def run_job(job: Job) -> None:
    """Run a claimed Job under its own correlation ID, then mark it done or failed.

    Never raises for the work's own failure: it is logged at ERROR with the traceback, the
    Job keeps a safe message, and the handler leaves nothing half-made.
    """
    handler = handler_for(job.kind)
    correlation = set_correlation_id(job.correlation_id or str(uuid.uuid4()))
    tenant = set_log_tenant(handler.tenant_host(job))
    started = monotonic()
    context = handler.log_context(job)
    try:
        logger.info("job started: job_id=%s kind=%s %s", job.pk, job.kind, context)
        try:
            handler.run(job)
        except Exception:  # every failure fails the Job; logged with its traceback
            activate_public_schema()
            logger.exception("job failed: job_id=%s kind=%s %s", job.pk, job.kind, context)
            _give_up(job, handler.failure_message, timezone.now())
            return
        activate_public_schema()
        job.state = JobState.DONE
        job.finished_at = timezone.now()
        job.locked_at = None
        job.save(update_fields=["state", "finished_at", "locked_at"])
        duration_ms = round((monotonic() - started) * _MS_PER_SECOND)
        logger.info(
            "job finished: job_id=%s kind=%s duration_ms=%s %s",
            job.pk,
            job.kind,
            duration_ms,
            context,
        )
    finally:
        activate_public_schema()
        reset_log_tenant(tenant)
        reset_correlation_id(correlation)


def run_next_job(timeout: timedelta, now: datetime | None = None) -> bool:
    """Recover stuck Jobs, then claim and run the next one.

    Returns:
        Whether a Job was run.
    """
    recover_stuck_jobs(timeout, now)
    job = claim_next_job(now)
    if job is None:
        return False
    run_job(job)
    return True


def _give_up(job: Job, message: str, now: datetime) -> None:
    job.state = JobState.FAILED
    job.error = message[:JOB_ERROR_MAX_LENGTH]
    job.finished_at = now
    job.locked_at = None
    job.save(update_fields=["state", "error", "finished_at", "locked_at"])
    handler = handler_for(job.kind)
    try:
        handler.gave_up(job)
    except Exception:  # the worker keeps going; logged with its traceback for a person to fix
        activate_public_schema()
        logger.exception(
            "job clean-up failed: job_id=%s kind=%s %s", job.pk, job.kind, handler.log_context(job)
        )
        return
    logger.info("job given up: job_id=%s kind=%s %s", job.pk, job.kind, handler.log_context(job))
