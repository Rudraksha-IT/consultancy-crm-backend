from __future__ import annotations


class AlreadyInBatchError(Exception):
    """The Student is already in this Batch."""


class LostStudentError(Exception):
    """A Lost Student can't join a Batch; they're brought back to Counselling first."""


class DayOutsideBatchError(Exception):
    """The day is before the Batch starts or after it ends."""


class NotInBatchError(Exception):
    """A Student marked isn't in the Batch."""
