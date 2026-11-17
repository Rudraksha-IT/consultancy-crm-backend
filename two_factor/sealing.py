"""Encrypt authenticator secrets before they are stored (issue 61).

The key is `TWO_FACTOR_SECRETS_KEY` (a Fernet key from Secrets Manager or SSM), never
DJANGO_SECRET_KEY, so the database alone never gives the secrets away.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.two_factor import is_fernet_key

NO_KEY_MESSAGE = "TWO_FACTOR_SECRETS_KEY is not set to a Fernet key."
CANT_OPEN_MESSAGE = "The sealed secret can't be opened with this key."


def secrets_key_is_set() -> bool:
    """Whether this server can seal authenticator secrets, so two-step login can be used."""
    key: str = getattr(settings, "TWO_FACTOR_SECRETS_KEY", "")
    return bool(key) and is_fernet_key(key)


def _fernet() -> Fernet:
    if not secrets_key_is_set():
        raise ImproperlyConfigured(NO_KEY_MESSAGE)
    return Fernet(settings.TWO_FACTOR_SECRETS_KEY.encode())


def seal_secret(secret: str) -> str:
    """Return `secret` encrypted and signed, as text to store.

    Raises:
        ImproperlyConfigured: No usable key is set.
    """
    return _fernet().encrypt(secret.encode()).decode()


def open_secret(sealed: str) -> str:
    """Return the secret `sealed` holds.

    Raises:
        ImproperlyConfigured: No usable key is set.
        ValueError: It was sealed with another key, or changed since.
    """
    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except InvalidToken as error:
        raise ValueError(CANT_OPEN_MESSAGE) from error
