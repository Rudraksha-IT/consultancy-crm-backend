from __future__ import annotations

from django.apps import AppConfig


class TasksConfig(AppConfig):
    """Tasks given to Staff members, optionally about a Student, per Consultancy schema."""

    name = "tasks"
