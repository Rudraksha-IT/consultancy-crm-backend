"""The pure parts of two-step login (issue 61): authenticator numbers and recovery codes.

Numbers follow TOTP (RFC 6238): 6 digits, a new one every 30 seconds, from a shared secret.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime

import pyotp

STEP_SECONDS = 30
# How many steps either side of now are accepted, for a phone clock a little off.
WINDOW_STEPS = 1
SECRET_LENGTH = 32
RECOVERY_CODE_COUNT = 10
RECOVERY_GROUPS = 3
RECOVERY_GROUP_LENGTH = 4
# No 0/o, 1/l/i: easy to read back off paper.
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
# Far longer than any code, so nothing longer is ever looked at.
RECOVERY_CODE_MAX_INPUT = 40

_NUMBER = re.compile(r"[0-9]{6}\Z", re.ASCII)
_RECOVERY = re.compile(
    rf"[{RECOVERY_ALPHABET}]{{{RECOVERY_GROUP_LENGTH * RECOVERY_GROUPS}}}\Z", re.ASCII
)
_SEPARATORS = re.compile(r"[\s-]+")


@dataclass(frozen=True)
class NumberCheck:
    """What checking a number found: the step it was made for, or None when refused."""

    accepted_step: int | None
    # True when the number was right but its step was already used (a replay).
    replayed: bool = False


def new_secret() -> str:
    """Return a new authenticator secret: 160 random bits in base32."""
    return pyotp.random_base32(length=SECRET_LENGTH)


def setup_uri(secret: str, account: str, issuer: str) -> str:
    """Return the `otpauth://` URI an authenticator app reads from the QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def step_at(moment: datetime) -> int:
    """Return the 30-second step `moment` falls in."""
    return int(moment.timestamp()) // STEP_SECONDS


def is_number_shaped(text: str) -> bool:
    """Whether `text` is exactly six ASCII digits."""
    return bool(_NUMBER.match(text))


def check_number(secret: str, number: str, *, at: datetime, last_step: int | None) -> NumberCheck:
    """Check `number` against the steps around `at`, refusing any step up to `last_step`.

    Returns:
        The accepted step (to keep as the new `last_step`), or None, marked `replayed` when the
        number was right for a step already used.
    """
    if not is_number_shaped(number):
        return NumberCheck(accepted_step=None)
    totp = pyotp.TOTP(secret)
    now_step = step_at(at)
    for step in range(now_step - WINDOW_STEPS, now_step + WINDOW_STEPS + 1):
        if hmac.compare_digest(totp.generate_otp(step), number):
            if last_step is not None and step <= last_step:
                return NumberCheck(accepted_step=None, replayed=True)
            return NumberCheck(accepted_step=step)
    return NumberCheck(accepted_step=None)


def _normalized(code: str) -> str:
    return _SEPARATORS.sub("", code.strip()).lower()


def is_recovery_code_shaped(text: str) -> bool:
    """Whether `text` could be a recovery code (ignoring case, spaces and hyphens)."""
    return len(text) <= RECOVERY_CODE_MAX_INPUT and bool(_RECOVERY.match(_normalized(text)))


def new_recovery_codes() -> list[str]:
    """Return `RECOVERY_CODE_COUNT` new, different recovery codes such as `abcd-efgh-jkmn`."""
    codes: set[str] = set()
    while len(codes) < RECOVERY_CODE_COUNT:
        groups = (
            "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUP_LENGTH))
            for _ in range(RECOVERY_GROUPS)
        )
        codes.add("-".join(groups))
    return sorted(codes)


def recovery_code_hash(code: str) -> str:
    """Return the hash a recovery code is kept as (60 random bits, so SHA-256 is enough)."""
    return hashlib.sha256(_normalized(code).encode()).hexdigest()
