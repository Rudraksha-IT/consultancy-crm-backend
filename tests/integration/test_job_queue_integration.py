"""The job queue and its worker (issue 60): a table in public, claimed with SKIP LOCKED."""

from __future__ import annotations

import os
import signal
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from django_tenants.utils import schema_context

from config.correlation import reset_correlation_id, set_correlation_id
from jobs.exceptions import UnknownJobKindError, UnsafeJobPayloadError
from jobs.models import Job, JobState
from jobs.registry import register_job_kind, unregister_job_kind
from jobs.services import claim_next_job, queue_job, recover_stuck_jobs, run_job
from tenancy.subdomains import build_host
from tests.conftest import JOB_TIMEOUT, PUBLIC_SCHEMA, JsonLogs, run_queued_jobs

KIND = "test_kind"
FAILURE_MESSAGE = "The test job could not finish."
SECRET_IN_ERROR = "smtp said: password=hunter2 for sita@everest.test"  # noqa: S105  # fake


@dataclass
class RecordingHandler:
    """A Job kind for these tests: records what it ran and gave up on."""

    ran: list[int] = field(default_factory=list)
    gave_up_on: list[int] = field(default_factory=list)
    action: Callable[[], None] | None = None
    failure_message: str = FAILURE_MESSAGE

    def run(self, job: Job) -> None:
        self.ran.append(job.pk)
        if self.action is not None:
            self.action()

    def gave_up(self, job: Job) -> None:
        self.gave_up_on.append(job.pk)

    def log_context(self, job: Job) -> str:
        return f"thing_id={job.payload.get('thing_id')}"

    def tenant_host(self, job: Job) -> str | None:
        return build_host("thing")


@pytest.fixture
def handler(db: None) -> Iterator[RecordingHandler]:
    """Register the test kind for one test."""
    recording = RecordingHandler()
    register_job_kind(KIND, recording)
    with schema_context(PUBLIC_SCHEMA):
        yield recording
    unregister_job_kind(KIND)


def queue(**payload: str | int | bool | None) -> Job:
    """Queue a Job of the test kind."""
    return queue_job(KIND, {"thing_id": 1, **payload})


class TestQueueJob:
    def test_a_job_waits_in_the_queue_with_the_requests_correlation_id(
        self, handler: RecordingHandler
    ) -> None:
        token = set_correlation_id("0b9f0e57-6a1b-4bb4-8f3e-3f0c2d4c9a11")
        try:
            job = queue(name="Everest")
        finally:
            reset_correlation_id(token)

        job.refresh_from_db()
        assert (job.state, job.attempts, job.max_attempts) == (JobState.QUEUED, 0, 3)
        assert job.payload == {"thing_id": 1, "name": "Everest"}
        assert job.correlation_id == "0b9f0e57-6a1b-4bb4-8f3e-3f0c2d4c9a11"

    @pytest.mark.parametrize(
        "payload",
        [
            {"admin_password": "x"},
            {"password": "x"},
            {"invite_code": "123456"},
            {"refresh_token": "x"},
            {"api_secret": "x"},
            {"signing_key": "x"},
            {"Name": "x"},
            {"name; DROP TABLE jobs_job": "x"},
            {"nested": {"a": 1}},
            {"items": [1, 2]},
            {"price": 1.5},
            {"long": "x" * 501},
        ],
    )
    def test_refuses_anything_but_ids_and_plain_values(
        self, handler: RecordingHandler, payload: dict[str, object]
    ) -> None:
        with pytest.raises(UnsafeJobPayloadError):
            queue_job(KIND, payload)  # type: ignore[arg-type]  # refused input on purpose

        assert not Job.objects.exists()

    def test_refuses_a_kind_nobody_runs(self, handler: RecordingHandler) -> None:
        with pytest.raises(UnknownJobKindError):
            queue_job("no_such_kind", {})


class TestClaim:
    def test_claims_the_oldest_queued_job_and_marks_it_running(
        self, handler: RecordingHandler
    ) -> None:
        first, second = queue(), queue()

        claimed = claim_next_job()

        assert claimed is not None
        assert claimed.pk == first.pk
        claimed.refresh_from_db()
        assert (claimed.state, claimed.attempts) == (JobState.RUNNING, 1)
        assert claimed.locked_at is not None
        assert claimed.started_at is not None
        second.refresh_from_db()
        assert second.state == JobState.QUEUED

    def test_nothing_to_claim(self, handler: RecordingHandler) -> None:
        assert claim_next_job() is None


class TestEachJobRunsOnce:
    """Two workers on two database connections: a job one is claiming is skipped by the other."""

    def test_a_job_locked_by_another_worker_is_skipped_not_waited_for(
        self, handler: RecordingHandler
    ) -> None:
        other = connections.create_connection("default")
        other.ensure_connection()
        other.connection.autocommit = True
        job_id = None
        try:
            with other.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.jobs_job (kind, payload, state, attempts, max_attempts,"
                    " created_at, error, correlation_id)"
                    " VALUES (%s, '{}'::jsonb, 'queued', 0, 3, now(), '', '') RETURNING id",
                    [KIND],
                )
                job_id = cursor.fetchone()[0]
            # The other worker is half-way through claiming it: the row is locked.
            other.connection.autocommit = False
            with other.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM public.jobs_job WHERE id = %s FOR UPDATE SKIP LOCKED",
                    [job_id],
                )
                assert cursor.fetchone() == (job_id,)
            with connections["default"].cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '2s'")

            assert claim_next_job() is None

            # The other worker finishes its claim: the job is running, so still not ours.
            with other.connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.jobs_job SET state = 'running', attempts = 1,"
                    " locked_at = now() WHERE id = %s",
                    [job_id],
                )
            other.connection.commit()
            assert claim_next_job() is None
            assert handler.ran == []
        finally:
            other.connection.rollback()
            other.connection.autocommit = True
            if job_id is not None:
                with other.connection.cursor() as cursor:
                    cursor.execute("DELETE FROM public.jobs_job WHERE id = %s", [job_id])
            other.close()


class TestRun:
    def test_a_job_that_works_is_done(self, handler: RecordingHandler) -> None:
        job = queue()

        assert run_queued_jobs() == 1

        job.refresh_from_db()
        assert handler.ran == [job.pk]
        assert (job.state, job.error) == (JobState.DONE, "")
        assert job.finished_at is not None

    def test_a_job_that_raises_fails_with_the_safe_message_and_is_cleaned_up(
        self, handler: RecordingHandler, json_logs: JsonLogs
    ) -> None:
        def explode() -> None:
            raise RuntimeError(SECRET_IN_ERROR)

        handler.action = explode
        job = queue()

        assert run_queued_jobs() == 1

        job.refresh_from_db()
        assert (job.state, job.error) == (JobState.FAILED, FAILURE_MESSAGE)
        assert handler.gave_up_on == [job.pk]
        failed = json_logs.only(f"job failed: job_id={job.pk} kind={KIND} thing_id=1")
        assert failed["level"] == "ERROR"
        assert "RuntimeError" in failed["exception"]
        assert "hunter2" not in json_logs.text()
        assert "sita@everest.test" not in json_logs.text()

    def test_a_failing_clean_up_is_logged_and_the_worker_goes_on(
        self, handler: RecordingHandler, json_logs: JsonLogs
    ) -> None:
        def explode() -> None:
            raise RuntimeError

        def explode_on_clean_up(job: Job) -> None:
            raise RuntimeError

        handler.action = explode
        handler.gave_up = explode_on_clean_up  # type: ignore[method-assign]  # test double
        first, second = queue(), queue()

        assert run_queued_jobs() == 2

        assert {first.pk, second.pk} == set(
            Job.objects.filter(state=JobState.FAILED).values_list("pk", flat=True)
        )
        assert json_logs.with_message(f"job clean-up failed: job_id={first.pk}")[0]["level"] == (
            "ERROR"
        )

    def test_logs_start_and_end_with_its_id_kind_duration_correlation_id_and_tenant(
        self, handler: RecordingHandler, json_logs: JsonLogs
    ) -> None:
        token = set_correlation_id("0b9f0e57-6a1b-4bb4-8f3e-3f0c2d4c9a11")
        try:
            job = queue()
        finally:
            reset_correlation_id(token)
        json_logs.clear()

        run_queued_jobs()

        started = json_logs.only(f"job started: job_id={job.pk} kind={KIND} thing_id=1")
        finished = json_logs.with_message(f"job finished: job_id={job.pk} kind={KIND} ")
        assert len(finished) == 1
        assert "duration_ms=" in finished[0]["message"]
        for entry in (started, finished[0]):
            assert entry["level"] == "INFO"
            assert entry["correlation_id"] == "0b9f0e57-6a1b-4bb4-8f3e-3f0c2d4c9a11"
            assert entry["tenant"] == build_host("thing")

    def test_a_job_queued_outside_a_request_gets_its_own_correlation_id(
        self, handler: RecordingHandler, json_logs: JsonLogs
    ) -> None:
        job = queue()
        claimed = claim_next_job()
        assert claimed is not None

        run_job(claimed)

        started = json_logs.only(f"job started: job_id={job.pk} kind={KIND} thing_id=1")
        assert started["correlation_id"] not in ("", "N/A")


class TestStuckJobs:
    def test_a_job_running_too_long_is_queued_again_then_failed_after_max_attempts(
        self, handler: RecordingHandler
    ) -> None:
        job = queue()
        later = timezone.now()
        for attempt in range(1, 4):
            claimed = claim_next_job(later)
            assert claimed is not None
            assert claimed.attempts == attempt
            # The worker dies (killed for memory): the job stays running.
            later += JOB_TIMEOUT + timedelta(seconds=1)
            assert recover_stuck_jobs(JOB_TIMEOUT, later) == 1
            job.refresh_from_db()
            expected = JobState.QUEUED if attempt < 3 else JobState.FAILED
            assert job.state == expected

        assert job.error == "The job stopped before finishing too many times."
        assert handler.gave_up_on == [job.pk]
        assert claim_next_job(later) is None

    def test_a_job_running_within_the_timeout_is_left_alone(
        self, handler: RecordingHandler
    ) -> None:
        job = queue()
        now = timezone.now()
        claim_next_job(now)

        assert recover_stuck_jobs(JOB_TIMEOUT, now + JOB_TIMEOUT - timedelta(seconds=1)) == 0

        job.refresh_from_db()
        assert job.state == JobState.RUNNING


class TestRunJobsCommand:
    def test_until_empty_runs_every_queued_job_one_at_a_time(
        self, handler: RecordingHandler
    ) -> None:
        first, second = queue(), queue()

        call_command("run_jobs", "--until-empty")

        assert handler.ran == [first.pk, second.pk]
        assert set(Job.objects.values_list("state", flat=True)) == {JobState.DONE}

    def test_sigterm_lets_the_current_job_finish_then_stops(
        self, handler: RecordingHandler, json_logs: JsonLogs
    ) -> None:
        handler.action = lambda: os.kill(os.getpid(), signal.SIGTERM)
        first, second = queue(), queue()
        before = signal.getsignal(signal.SIGTERM)

        call_command("run_jobs")

        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.state, second.state) == (JobState.DONE, JobState.QUEUED)
        assert json_logs.with_message("worker stopped")
        assert signal.getsignal(signal.SIGTERM) == before
