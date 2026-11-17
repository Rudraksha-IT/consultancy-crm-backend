from __future__ import annotations

from django.apps import AppConfig


class JobsConfig(AppConfig):
    """The queue of slow work done by the `worker` (issue 60), in the shared public schema."""

    name = "jobs"
