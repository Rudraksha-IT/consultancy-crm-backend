from __future__ import annotations

from staff.passwords import PasswordRule


class StaffNotAuthenticatedError(Exception):
    """Staff-only code was reached by a request with no logged-in Staff member."""


class PasswordRulesNotMetError(Exception):
    """A new password breaks one or more of the password rules."""

    def __init__(self, unmet: list[PasswordRule]) -> None:
        super().__init__("The password doesn't meet every rule.")
        self.unmet = unmet


class EmailTakenError(Exception):
    """Another Staff member of this Consultancy already uses the email."""


class RoleNotAvailableError(Exception):
    """The Role can't be given here (Teacher while the Batches Module is off)."""


class EmployeeChangeRefusedError(Exception):
    """The change doesn't fit the person's account; `message` says why, in screen words."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WrongCurrentPasswordError(Exception):
    """The current password typed when changing it isn't the person's password."""
