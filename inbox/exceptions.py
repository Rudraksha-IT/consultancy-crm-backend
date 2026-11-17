"""What the Inbox services refuse."""

from __future__ import annotations


class PersonHasLeftError(Exception):
    """The person written to no longer works here, so can't be written to (spec 130)."""
