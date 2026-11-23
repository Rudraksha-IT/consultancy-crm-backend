from __future__ import annotations


class PlatformAdminNotAuthenticatedError(Exception):
    """Platform-only code was reached by a request with no logged-in Platform Admin."""


class PlatformAdminDetailsError(Exception):
    """A Platform Admin's name, email or password is refused (issue 59).

    `problems` maps each refused field ("name", "email", "password") to a short message that
    never repeats the value.
    """

    def __init__(self, problems: dict[str, str]) -> None:
        super().__init__(", ".join(problems))
        self.problems = problems


class WrongCurrentPlatformPasswordError(Exception):
    """The current password given on the Account page isn't the Platform Admin's."""


class TooManyPasswordTriesError(Exception):
    """Too many wrong current passwords on the Account page; wait before trying again."""


class TestEmailLimitError(Exception):
    """A Platform Admin has sent too many test emails in the last hour."""

    __test__ = False  # not a pytest test class, despite its name


class SavedLogosFullError(Exception):
    """The most saved product logos are kept already (ticket 07); one must be removed first."""
