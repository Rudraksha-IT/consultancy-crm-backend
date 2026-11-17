"""Seal provider secrets before they're stored, and open them only to send (spec 88).

The key is `PROVIDER_SECRETS_KEY` from the environment, never DJANGO_SECRET_KEY, so the database
alone never reveals an office's SMS token or email password.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

KEY_MIN_LENGTH = 32
CANT_OPEN_MESSAGE = "The sealed secret can't be opened with this key."


def _fernet() -> Fernet:
    key: str = getattr(settings, "PROVIDER_SECRETS_KEY", "")
    if len(key) < KEY_MIN_LENGTH:
        message = f"PROVIDER_SECRETS_KEY must be at least {KEY_MIN_LENGTH} characters."
        raise ImproperlyConfigured(message)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def seal(secret: str) -> str:
    """Return `secret` encrypted and signed, as text to store."""
    return _fernet().encrypt(secret.encode()).decode()


def unseal(sealed: str) -> str:
    """Return the secret `sealed` holds.

    Raises:
        ValueError: it was sealed with another key, or changed since.
    """
    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except InvalidToken as error:
        raise ValueError(CANT_OPEN_MESSAGE) from error
