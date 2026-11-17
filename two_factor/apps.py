from __future__ import annotations

from django.apps import AppConfig


class TwoFactorConfig(AppConfig):
    """Two-step login (issue 61): authenticator secrets, recovery codes and challenges.

    Kept in the schema the person belongs to: public for Platform Admins, each Consultancy's
    own for its Staff (issue 62).
    """

    name = "two_factor"
