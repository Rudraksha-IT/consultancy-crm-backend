from __future__ import annotations


class TooManyEntriesError(Exception):
    """A Student already has the most qualifications, or test scores, allowed."""


class StageChangeRefusedError(Exception):
    """The Stage can't be moved this way by hand (see `students.stages`)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StudentChangedError(Exception):
    """Someone else changed the Student's counsellor while this change was on its way."""


class SameCounsellorError(Exception):
    """A Transfer to the counsellor the Student already has."""
