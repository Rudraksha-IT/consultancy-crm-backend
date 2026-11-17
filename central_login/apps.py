from __future__ import annotations

from django.apps import AppConfig


class CentralLoginConfig(AppConfig):
    """Central login (issue 44): the index of Staff email hashes, in the shared public schema."""

    name = "central_login"
