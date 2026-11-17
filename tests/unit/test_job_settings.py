"""The worker's settings (issue 60): JOB_POLL_SECONDS and JOB_TIMEOUT_SECONDS."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.jobs import JobSettings, read_job_settings


class TestReadJobSettings:
    def test_defaults_check_every_5_seconds_and_call_a_job_stuck_after_15_minutes(self) -> None:
        assert read_job_settings({}) == JobSettings(poll_seconds=5, timeout_seconds=900)

    def test_reads_both_values(self) -> None:
        environ = {"JOB_POLL_SECONDS": "60", "JOB_TIMEOUT_SECONDS": "60"}
        assert read_job_settings(environ) == JobSettings(poll_seconds=60, timeout_seconds=60)

    @pytest.mark.parametrize("value", ["0", "61", "-1", "5.5", "five", "", " 5", "1e1"])
    def test_refuses_a_poll_outside_1_to_60(self, value: str) -> None:
        with pytest.raises(ImproperlyConfigured, match="JOB_POLL_SECONDS"):
            read_job_settings({"JOB_POLL_SECONDS": value})

    @pytest.mark.parametrize("value", ["59", "86401", "-900", "nine hundred", ""])
    def test_refuses_a_timeout_outside_60_to_86400(self, value: str) -> None:
        with pytest.raises(ImproperlyConfigured, match="JOB_TIMEOUT_SECONDS"):
            read_job_settings({"JOB_TIMEOUT_SECONDS": value})
