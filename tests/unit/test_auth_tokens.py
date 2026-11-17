"""Small pure pieces of token login: the Bearer header and who is on the request."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from auth_tokens.exceptions import InvalidTokenError
from auth_tokens.headers import MAX_TOKEN_LENGTH, read_bearer_token
from auth_tokens.models import IssuedRefreshToken, TokenKind
from platform_admin.auth import get_request_platform_admin
from platform_admin.exceptions import PlatformAdminNotAuthenticatedError


class TestReadBearerToken:
    def test_no_header_means_no_token(self) -> None:
        assert read_bearer_token({}) is None

    def test_returns_the_token(self) -> None:
        assert read_bearer_token({"Authorization": "Bearer abc.def.ghi"}) == "abc.def.ghi"

    def test_accepts_the_longest_allowed_token(self) -> None:
        token = "a" * MAX_TOKEN_LENGTH

        assert read_bearer_token({"Authorization": f"Bearer {token}"}) == token

    @pytest.mark.parametrize(
        "header",
        ["", "Bearer", "Bearer ", "bearer x", "Basic x", "Bearer x y", f"Bearer {'a' * 2049}"],
    )
    def test_refuses_anything_else(self, header: str) -> None:
        with pytest.raises(InvalidTokenError):
            read_bearer_token({"Authorization": header})


class TestIssuedRefreshToken:
    def test_names_the_kind_and_person_never_the_token(self) -> None:
        record = IssuedRefreshToken(jti="secret-jti", kind=TokenKind.STAFF, person_id=7)

        assert str(record) == "staff 7"


class TestGetRequestPlatformAdmin:
    def test_refuses_a_request_without_a_platform_admin(self) -> None:
        request = Request(APIRequestFactory().get("/"))
        request.user = AnonymousUser()

        with pytest.raises(PlatformAdminNotAuthenticatedError):
            get_request_platform_admin(request)
