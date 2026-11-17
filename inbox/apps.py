from __future__ import annotations

from django.apps import AppConfig


class InboxConfig(AppConfig):
    """Inbox: one-to-one messages between two Staff of the same Consultancy (spec 128-130)."""

    name = "inbox"
