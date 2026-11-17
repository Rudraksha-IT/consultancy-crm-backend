from __future__ import annotations

from django.apps import AppConfig


class TransferRequestsConfig(AppConfig):
    """Transfer Requests: asking to take over a Student, and the answer (spec 44a-44e, issue 57)."""

    name = "transfer_requests"
