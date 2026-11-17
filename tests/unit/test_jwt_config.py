"""How long access tokens last can only be shortened from the environment."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.jwt import read_access_token_lifetime


class TestReadAccessTokenLifetime:
    def test_is_15_minutes_by_default(self) -> None:
        assert read_access_token_lifetime({}) == timedelta(minutes=15)

    @pytest.mark.parametrize("minutes", ["1", "5", "15"])
    def test_can_be_shortened(self, minutes: str) -> None:
        environ = {"JWT_ACCESS_TOKEN_MINUTES": minutes}

        assert read_access_token_lifetime(environ) == timedelta(minutes=int(minutes))

    @pytest.mark.parametrize("minutes", ["0", "16", "600", "-1", "1.5", "", "ten", " 5"])
    def test_refuses_anything_else_without_echoing_it(self, minutes: str) -> None:
        with pytest.raises(ImproperlyConfigured) as raised:
            read_access_token_lifetime({"JWT_ACCESS_TOKEN_MINUTES": minutes})

        assert "JWT_ACCESS_TOKEN_MINUTES" in str(raised.value)
        assert minutes.strip() == "" or minutes not in str(raised.value)
