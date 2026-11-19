from __future__ import annotations


class NoContactEmailError(Exception):
    """The office has no contact email yet, so nothing can be sent to Students (ADR 0023)."""
