from __future__ import annotations


class ChallengeEndedError(Exception):
    """The two-step challenge can't be used; the person logs in again.

    It is unknown, forged, expired, used, ended by wrong tries, for another step, or from
    another host.
    """


class WrongNumberError(Exception):
    """The number (or recovery code) was refused; the challenge may still be used."""


class TooManyWrongNumbersError(Exception):
    """Too many wrong numbers for this person in a short time; wait before trying again."""


class TwoFactorUnavailableError(Exception):
    """This server has no TWO_FACTOR_SECRETS_KEY, so two-step login can't be switched on."""
