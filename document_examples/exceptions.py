from __future__ import annotations


class TooManyExamplesError(Exception):
    """The Document Type already has as many example files in use as one type may have."""
