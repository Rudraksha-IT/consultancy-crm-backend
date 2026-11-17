"""The queue of slow work, a table in the public schema (issue 60). No Redis, no Celery."""

from __future__ import annotations

from django.db import models
from django.utils import timezone

JOB_KIND_MAX_LENGTH = 50
JOB_ERROR_MAX_LENGTH = 200
JOB_STATE_MAX_LENGTH = 10
CORRELATION_ID_MAX_LENGTH = 36
DEFAULT_MAX_ATTEMPTS = 3


class JobState(models.TextChoices):
    """Where a Job is: waiting, being run by a worker, finished, or given up on."""

    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class Job(models.Model):
    """One piece of slow work for the `worker` (`manage.py run_jobs`).

    `payload` holds ids and plain values only, never a password, token or Code (checked by
    `jobs.services.queue_job`). `error` is a short safe message, never a traceback.
    """

    kind = models.CharField(max_length=JOB_KIND_MAX_LENGTH)
    payload = models.JSONField(default=dict)
    state = models.CharField(
        max_length=JOB_STATE_MAX_LENGTH,
        choices=JobState.choices,
        default=JobState.QUEUED,
        db_index=True,
    )
    # Raised each time a worker takes the job; a stuck job is taken again while attempts remain.
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=DEFAULT_MAX_ATTEMPTS)
    created_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=JOB_ERROR_MAX_LENGTH, blank=True, default="")
    # The Platform Admin whose request queued it; None for work the system queues itself.
    created_by = models.ForeignKey(
        "platform_admin.PlatformAdmin",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    # The queuing request's correlation ID, so the request and the job join up in the logs.
    correlation_id = models.CharField(max_length=CORRELATION_ID_MAX_LENGTH, blank=True, default="")

    class Meta:
        ordering = ("pk",)

    def __str__(self) -> str:
        return f"{self.kind} #{self.pk} ({self.state})"
