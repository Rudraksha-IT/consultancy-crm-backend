from __future__ import annotations

from django.apps import AppConfig


class StudentsConfig(AppConfig):
    """Students, from first enquiry (Lead) onward, stored in each Consultancy's own schema."""

    name = "students"
