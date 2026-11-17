"""Two-step login settings read from the environment (issue 61)."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field

from django.core.exceptions import ImproperlyConfigured

DEFAULT_VAR = "PLATFORM_ADMIN_TWO_FACTOR_DEFAULT"
STAFF_DEFAULT_VAR = "STAFF_TWO_FACTOR_DEFAULT"
KEY_VAR = "TWO_FACTOR_SECRETS_KEY"
FERNET_KEY_BYTES = 32
_CHOICES = {"true": True, "false": False}


@dataclass(frozen=True)
class TwoFactorSettings:
    """Whether a new Platform Admin, and new Staff, start with two-step login on; the key."""

    platform_admin_default: bool
    staff_default: bool
    # Never shown in a repr, so a settings dump or a traceback can't print it.
    secrets_key: str = field(repr=False)


def read_two_factor_settings(environ: Mapping[str, str]) -> TwoFactorSettings:
    """Return the two-step login settings set in `environ`.

    `PLATFORM_ADMIN_TWO_FACTOR_DEFAULT` and `STAFF_TWO_FACTOR_DEFAULT` (issue 62) are `true` or
    `false` (default `false`). `TWO_FACTOR_SECRETS_KEY` is empty or a Fernet key; it is needed
    when either default is `true`.

    Raises:
        ImproperlyConfigured: A value is refused (the message names the variable, never a value).
    """
    platform_admin_default = _read_choice(environ, DEFAULT_VAR)
    staff_default = _read_choice(environ, STAFF_DEFAULT_VAR)
    key = environ.get(KEY_VAR, "")
    if key and not is_fernet_key(key):
        message = f"{KEY_VAR} must be a Fernet key (32 bytes in URL-safe base64)."
        raise ImproperlyConfigured(message)
    for name, value in ((DEFAULT_VAR, platform_admin_default), (STAFF_DEFAULT_VAR, staff_default)):
        if value and not key:
            message = f"{KEY_VAR} is needed when {name} is true."
            raise ImproperlyConfigured(message)
    return TwoFactorSettings(
        platform_admin_default=platform_admin_default, staff_default=staff_default, secrets_key=key
    )


def _read_choice(environ: Mapping[str, str], name: str) -> bool:
    value = environ.get(name, "false")
    if value not in _CHOICES:
        message = f"{name} must be true or false."
        raise ImproperlyConfigured(message)
    return _CHOICES[value]


def is_fernet_key(key: str) -> bool:
    """Whether `key` is 32 bytes written in URL-safe base64, as `Fernet.generate_key()` makes."""
    try:
        decoded = base64.urlsafe_b64decode(key.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return False
    return len(decoded) == FERNET_KEY_BYTES and base64.urlsafe_b64encode(decoded).decode() == key
