"""Login with JWTs (ADR 0003).

Every token belongs to one Consultancy (or the Platform) and stops working at once after logout,
rotation or a sign-out everywhere.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django_tenants.utils import schema_context, tenant_context
from pytest_django.fixtures import Settings
from rest_framework import status
from rest_framework.test import APIClient

from auth_tokens.middleware import TokenConsultancyMiddleware
from auth_tokens.models import IssuedRefreshToken
from auth_tokens.services import issue_tokens, sign_out_everywhere
from platform_admin.models import PlatformAdmin
from staff.models import Role, Staff, StaffStatus
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    PUBLIC_SCHEMA,
    TEST_JWT_SIGNING_KEY,
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    use_access_token,
)

LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
LOGOUT_URL = "/api/auth/logout/"
CONTEXT_URL = "/api/context/"
PLATFORM_LOGIN_URL = "/api/platform/auth/login/"
PLATFORM_REFRESH_URL = "/api/platform/auth/refresh/"
PLATFORM_LOGOUT_URL = "/api/platform/auth/logout/"
PLATFORM_ME_URL = "/api/platform/me/"
REFUSED = {"detail": "Invalid or expired token."}
ACCESS_MINUTES = 15
REFRESH_DAYS = 7
OTHER_KEY = "another-signing-key-that-is-long-enough-0123"


def claims_of(token: str) -> dict[str, Any]:
    """Return a token's claims, checking its signature with the test key."""
    decoded: dict[str, Any] = jwt.decode(token, TEST_JWT_SIGNING_KEY, algorithms=["HS256"])
    return decoded


def forge(claims: dict[str, Any], key: str = TEST_JWT_SIGNING_KEY) -> str:
    """Sign an access token with arbitrary claims (plus a type, id and lifetime)."""
    now = datetime.now(tz=UTC)
    payload = {
        "token_type": "access",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **claims,
    }
    return jwt.encode(payload, key, algorithm="HS256")


def staff_claims(abc: ConsultancyContext, staff: Staff) -> dict[str, Any]:
    """The claims a real access token for this Staff member carries."""
    return {
        "tenant_schema": abc.consultancy.schema_name,
        "kind": "staff",
        "user_id": staff.pk,
        "auth_version": 0,
        "device": uuid.uuid4().hex,
    }


def log_in(client: APIClient, email: str, url: str = LOGIN_URL) -> dict[str, Any]:
    """Log in with the test password (the Platform's on its login) and return the JSON answer."""
    password = PLATFORM_TEST_PASSWORD if url == PLATFORM_LOGIN_URL else TEST_PASSWORD
    response = client.post(url, {"email": email, "password": password}, format="json")
    assert response.status_code == status.HTTP_200_OK
    body: dict[str, Any] = response.json()
    return body


def context_status(client: APIClient, access: str, url: str = CONTEXT_URL) -> int:
    """Call an endpoint with this access token and return the status code."""
    use_access_token(client, access)
    return client.get(url).status_code


class TestStaffLogin:
    def test_answers_with_both_tokens_and_the_context(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        body = log_in(abc.client, counsellor.email)

        assert set(body) == {"access", "refresh", "context"}
        assert body["context"]["staff"]["email"] == counsellor.email
        assert context_status(abc.new_client(), body["access"]) == status.HTTP_200_OK

    def test_tokens_carry_the_consultancy_kind_version_and_id(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        body = log_in(abc.client, counsellor.email)

        for token, token_type in ((body["access"], "access"), (body["refresh"], "refresh")):
            claims = claims_of(token)
            assert claims["token_type"] == token_type
            assert claims["tenant_schema"] == "abcedu"
            assert claims["kind"] == "staff"
            assert claims["auth_version"] == 0
            assert claims["user_id"] == counsellor.pk
            assert claims["jti"]
        assert claims_of(body["access"])["device"] == claims_of(body["refresh"])["device"]

    def test_each_login_is_a_new_device(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        first = log_in(abc.client, counsellor.email)
        second = log_in(abc.new_client(), counsellor.email)

        assert claims_of(first["access"])["device"] != claims_of(second["access"])["device"]

    def test_access_lasts_15_minutes_and_refresh_7_days(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        body = log_in(abc.client, counsellor.email)

        access, refresh = claims_of(body["access"]), claims_of(body["refresh"])
        assert access["exp"] - access["iat"] == ACCESS_MINUTES * 60
        assert refresh["exp"] - refresh["iat"] == REFRESH_DAYS * 24 * 60 * 60

    def test_no_cookie_is_set(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        response = abc.client.post(
            LOGIN_URL, {"email": counsellor.email, "password": TEST_PASSWORD}, format="json"
        )

        assert not response.cookies

    def test_the_csrf_endpoint_is_gone(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        assert abc.client.get("/api/auth/csrf/").status_code == status.HTTP_404_NOT_FOUND

    def test_a_stored_record_lets_the_refresh_token_be_refused_later(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        body = log_in(abc.client, counsellor.email)

        with tenant_context(abc.consultancy):
            record = IssuedRefreshToken.objects.get(jti=claims_of(body["refresh"])["jti"])
        assert (record.kind, record.person_id, record.revoked_at) == ("staff", counsellor.pk, None)


class TestRefresh:
    def test_gives_a_new_pair_that_works(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        tokens = abc.tokens_for(counsellor)

        response = abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert set(response.json()) == {"access", "refresh"}
        assert response.json()["refresh"] != tokens.refresh
        assert context_status(abc.new_client(), response.json()["access"]) == status.HTTP_200_OK

    def test_the_new_pair_stays_on_the_same_device(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        tokens = abc.tokens_for(abc.add_staff(Role.COUNSELLOR))

        renewed = abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json").json()

        device = claims_of(tokens.access)["device"]
        assert claims_of(renewed["access"])["device"] == device
        assert claims_of(renewed["refresh"])["device"] == device
        with tenant_context(abc.consultancy):
            records = IssuedRefreshToken.objects.order_by("id")
            assert {record.device_id for record in records} == {device}
            assert records[0].signed_in_at == records[1].signed_in_at

    def test_rotation_refuses_the_old_refresh_token_after_use(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        tokens = abc.tokens_for(abc.add_staff(Role.COUNSELLOR))
        abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")

        response = abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == REFUSED

    def test_an_access_token_is_not_a_refresh_token(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        tokens = abc.tokens_for(abc.add_staff(Role.COUNSELLOR))

        response = abc.client.post(REFRESH_URL, {"refresh": tokens.access}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_refresh_token_is_not_an_access_token(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        tokens = abc.tokens_for(abc.add_staff(Role.COUNSELLOR))

        assert context_status(abc.client, tokens.refresh) == status.HTTP_401_UNAUTHORIZED

    def test_deactivated_staff_cannot_refresh(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        tokens = abc.tokens_for(counsellor)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=counsellor.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_expired_refresh_token_is_refused(
        self, make_consultancy: ConsultancyFactory, settings: Settings
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        settings.JWT_REFRESH_TOKEN_LIFETIME = timedelta(seconds=-1)
        tokens = abc.tokens_for(abc.add_staff(Role.COUNSELLOR))

        response = abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestLogout:
    def test_refuses_the_refresh_token_afterwards(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        tokens = abc.tokens_for(abc.add_staff(Role.FRONT_DESK))

        response = abc.client.post(LOGOUT_URL, {"refresh": tokens.refresh}, format="json")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        refreshed = abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")
        assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED

    def test_leaves_the_persons_other_devices_signed_in(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        laptop, phone = abc.tokens_for(counsellor), abc.tokens_for(counsellor)

        abc.client.post(LOGOUT_URL, {"refresh": laptop.refresh}, format="json")

        refreshed = abc.client.post(REFRESH_URL, {"refresh": phone.refresh}, format="json")
        assert refreshed.status_code == status.HTTP_200_OK

    def test_refuses_a_token_it_cannot_read(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.post(LOGOUT_URL, {"refresh": "not-a-token"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == REFUSED


class TestAccessToken:
    def test_nobody_logged_in_is_401_with_a_bearer_challenge(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.headers["WWW-Authenticate"].startswith("Bearer")

    def test_an_expired_access_token_is_refused(
        self, make_consultancy: ConsultancyFactory, settings: Settings
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        settings.JWT_ACCESS_TOKEN_LIFETIME = timedelta(seconds=-1)
        tokens = abc.tokens_for(abc.add_staff(Role.COUNSELLOR))

        use_access_token(abc.client, tokens.access)
        response = abc.client.get(CONTEXT_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == REFUSED

    def test_a_tampered_token_is_refused(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        admin = abc.add_staff(Role.ADMIN)
        header, payload, signature = abc.tokens_for(counsellor).access.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        claims["user_id"] = admin.pk
        forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()

        status_code = context_status(abc.client, f"{header}.{forged}.{signature}")

        assert status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_token_signed_with_another_key_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        token = forge(staff_claims(abc, counsellor), key=OTHER_KEY)

        assert context_status(abc.client, token) == status.HTTP_401_UNAUTHORIZED

    def test_an_unsigned_token_is_refused(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        claims = {**staff_claims(abc, counsellor), "token_type": "access", "jti": "x"}
        claims["exp"] = int((datetime.now(tz=UTC) + timedelta(minutes=5)).timestamp())

        token = jwt.encode(claims, key="", algorithm="none")

        assert context_status(abc.client, token) == status.HTTP_401_UNAUTHORIZED

    def test_a_token_without_tenant_schema_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        claims = staff_claims(abc, counsellor)
        del claims["tenant_schema"]

        response_status = context_status(abc.client, forge(claims))

        assert response_status == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        ("claim", "value"),
        [
            ("tenant_schema", 7),
            ("kind", "support_access"),
            ("kind", None),
            ("user_id", "1"),
            ("user_id", True),
            ("auth_version", "0"),
            ("jti", 5),
            ("device", 5),
            ("device", None),
        ],
    )
    def test_a_token_with_a_wrong_claim_type_is_refused(
        self, make_consultancy: ConsultancyFactory, claim: str, value: object
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        claims = {**staff_claims(abc, counsellor), claim: value}

        assert context_status(abc.client, forge(claims)) == status.HTTP_401_UNAUTHORIZED

    def test_a_correctly_forged_token_works_so_the_refusals_above_are_meaningful(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        status_code = context_status(abc.client, forge(staff_claims(abc, counsellor)))

        assert status_code == status.HTTP_200_OK

    def test_a_staff_kind_token_with_the_public_schema_is_refused_on_a_consultancy(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        claims = {**staff_claims(abc, counsellor), "tenant_schema": PUBLIC_SCHEMA}

        assert context_status(abc.client, forge(claims)) == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize(
        "header", ["Basic abc", "Bearer", "Bearer ", "bearer abc", "Token abc", "Bearer a b"]
    )
    def test_a_malformed_authorization_header_is_refused(
        self, make_consultancy: ConsultancyFactory, header: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.get(CONTEXT_URL, HTTP_AUTHORIZATION=header)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_over_long_bearer_token_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        assert context_status(abc.client, "a" * 5000) == status.HTTP_401_UNAUTHORIZED

    def test_deactivated_staff_are_refused(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.log_in_as(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=counsellor.pk).update(status=StaffStatus.DEACTIVATED)

        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_token_for_a_removed_staff_member_is_refused(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.log_in_as(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=counsellor.pk).delete()

        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_bad_token_is_refused_even_on_an_open_endpoint(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.get("/api/consultancy/", HTTP_AUTHORIZATION="Bearer nonsense")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestSignOutEverywhere:
    def test_stops_an_unexpired_access_token_at_once(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.log_in_as(Role.COUNSELLOR)

        with tenant_context(abc.consultancy):
            sign_out_everywhere(counsellor)

        assert abc.client.get(CONTEXT_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_refuses_every_outstanding_refresh_token(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        laptop, phone = abc.tokens_for(counsellor), abc.tokens_for(counsellor)

        with tenant_context(abc.consultancy):
            sign_out_everywhere(counsellor)

        for tokens in (laptop, phone):
            response = abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_new_login_afterwards_works(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            sign_out_everywhere(counsellor)

        body = log_in(abc.client, counsellor.email)

        assert claims_of(body["access"])["auth_version"] == 1
        assert context_status(abc.new_client(), body["access"]) == status.HTTP_200_OK

    def test_leaves_other_staff_signed_in(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        admin_client = abc.new_client()
        admin = abc.add_staff(Role.ADMIN)
        use_access_token(admin_client, abc.tokens_for(admin).access)

        with tenant_context(abc.consultancy):
            sign_out_everywhere(counsellor)

        assert admin_client.get(CONTEXT_URL).status_code == status.HTTP_200_OK

    def test_works_for_a_platform_admin(self, platform_site: PlatformContext) -> None:
        admin = platform_site.log_in()

        with schema_context(PUBLIC_SCHEMA):
            sign_out_everywhere(admin)

        response = platform_site.client.get(PLATFORM_ME_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_is_logged_with_ids_only(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        with tenant_context(abc.consultancy):
            sign_out_everywhere(counsellor)

        json_logs.only(f"Signed out everywhere: kind=staff, person_id={counsellor.pk}")


class TestTokensAreIssuedOnlyWhereThePersonBelongs:
    def test_staff_get_no_tokens_on_the_public_schema(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        with schema_context(PUBLIC_SCHEMA), pytest.raises(ValueError, match="Staff"):
            issue_tokens(counsellor)

    def test_platform_admins_get_no_tokens_inside_a_consultancy(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        admin = platform_site.add_admin()

        with tenant_context(abc.consultancy), pytest.raises(ValueError, match="Platform Admin"):
            issue_tokens(admin)


class TestPlatformTokens:
    def test_login_refresh_and_logout(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()

        body = log_in(platform_site.client, admin.email, url=PLATFORM_LOGIN_URL)
        refreshed = platform_site.client.post(
            PLATFORM_REFRESH_URL, {"refresh": body["refresh"]}, format="json"
        ).json()
        logged_out = platform_site.client.post(
            PLATFORM_LOGOUT_URL, {"refresh": refreshed["refresh"]}, format="json"
        )

        assert set(body) == {"access", "refresh", "platform_admin"}
        assert claims_of(body["access"])["tenant_schema"] == PUBLIC_SCHEMA
        assert claims_of(body["access"])["kind"] == "platform_admin"
        assert context_status(platform_site.new_client(), refreshed["access"], PLATFORM_ME_URL) == (
            status.HTTP_200_OK
        )
        assert logged_out.status_code == status.HTTP_204_NO_CONTENT
        again = platform_site.client.post(
            PLATFORM_REFRESH_URL, {"refresh": refreshed["refresh"]}, format="json"
        )
        assert again.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_platform_token_is_refused_on_a_consultancy_host(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.add_staff(Role.ADMIN)  # Same primary key as the Platform Admin.
        tokens = platform_site.tokens_for(platform_site.add_admin())

        refreshed = abc.client.post(REFRESH_URL, {"refresh": tokens.refresh}, format="json")

        assert context_status(abc.client, tokens.access) == status.HTTP_401_UNAUTHORIZED
        assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_platform_kind_token_naming_a_consultancy_schema_is_refused(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        staff = abc.add_staff(Role.ADMIN)
        claims = {**staff_claims(abc, staff), "kind": "platform_admin"}

        assert context_status(abc.client, forge(claims)) == status.HTTP_401_UNAUTHORIZED

    def test_a_staff_token_is_refused_on_the_platform_host(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        platform_site.add_admin()  # Same primary key as the Staff member.
        tokens = abc.tokens_for(abc.add_staff(Role.ADMIN))

        refreshed = platform_site.client.post(
            PLATFORM_REFRESH_URL, {"refresh": tokens.refresh}, format="json"
        )

        assert context_status(platform_site.client, tokens.access, PLATFORM_ME_URL) == (
            status.HTTP_401_UNAUTHORIZED
        )
        assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED

    def test_an_inactive_platform_admin_is_refused(self, platform_site: PlatformContext) -> None:
        admin = platform_site.log_in()
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=admin.pk).update(is_active=False)

        response = platform_site.client.get(PLATFORM_ME_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestRefusedInput:
    @pytest.mark.parametrize("url", [REFRESH_URL, LOGOUT_URL])
    @pytest.mark.parametrize(
        "body",
        [{}, {"refresh": None}, {"refresh": 12}, {"refresh": ["a"]}, {"refresh": "x" * 4097}],
    )
    def test_missing_wrong_type_and_over_long_are_400(
        self, make_consultancy: ConsultancyFactory, url: str, body: dict[str, Any]
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.post(url, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.json()) == {"refresh"}

    @pytest.mark.parametrize("url", [REFRESH_URL, LOGOUT_URL])
    @pytest.mark.parametrize(
        "value",
        [
            "<script>alert(1)</script>",
            "'; DROP TABLE staff_staff; --",
            "body{background:url(javascript:alert(1))}",
            "eyJhbGciOiJub25lIn0.eyJ1c2VyX2lkIjoxfQ.",
        ],
    )
    def test_injection_shaped_tokens_are_refused(
        self, make_consultancy: ConsultancyFactory, url: str, value: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.client.post(url, {"refresh": value}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == REFUSED

    def test_unknown_fields_are_ignored(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)
        tokens = abc.tokens_for(counsellor)

        response = abc.client.post(
            REFRESH_URL,
            {"refresh": tokens.refresh, "kind": "platform_admin", "user_id": 999},
            format="json",
        )

        assert claims_of(response.json()["access"])["kind"] == "staff"
        assert claims_of(response.json()["access"])["user_id"] == counsellor.pk

    @pytest.mark.parametrize("url", [LOGIN_URL, REFRESH_URL, LOGOUT_URL])
    def test_only_post_is_allowed(self, make_consultancy: ConsultancyFactory, url: str) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        assert abc.client.get(url).status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert abc.client.put(url, {}, format="json").status_code == (
            status.HTTP_405_METHOD_NOT_ALLOWED
        )


class TestTokensAreNeverLogged:
    def test_login_refresh_and_logout_log_ids_only(
        self, make_consultancy: ConsultancyFactory, json_logs: JsonLogs
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        body = log_in(abc.client, counsellor.email)
        refreshed = abc.client.post(REFRESH_URL, {"refresh": body["refresh"]}, format="json")
        abc.client.post(LOGOUT_URL, {"refresh": refreshed.json()["refresh"]}, format="json")

        json_logs.only(f"Staff logged out: staff_id={counsellor.pk}")
        for token in (body["access"], body["refresh"], *refreshed.json().values()):
            assert token not in json_logs.text()
            assert token.split(".")[2] not in json_logs.text()


def _passes(_: HttpRequest) -> HttpResponse:
    return HttpResponse()


class TestStartup:
    @pytest.mark.parametrize("key", ["", "x" * 31])
    def test_refuses_to_start_without_a_long_enough_signing_key(
        self, settings: Settings, key: str
    ) -> None:
        settings.JWT_SIGNING_KEY = key

        with pytest.raises(ImproperlyConfigured, match="JWT_SIGNING_KEY"):
            TokenConsultancyMiddleware(_passes)

    def test_starts_with_a_32_byte_key(self, settings: Settings) -> None:
        settings.JWT_SIGNING_KEY = "k" * 32

        TokenConsultancyMiddleware(_passes)

    def test_the_signing_key_is_not_the_django_secret_key(self) -> None:
        assert settings.JWT_SIGNING_KEY != settings.SECRET_KEY

    def test_the_token_check_runs_right_after_the_consultancy_is_resolved(self) -> None:
        host = settings.MIDDLEWARE.index("tenancy.middleware.ConsultancyHostMiddleware")
        assert settings.MIDDLEWARE[host + 1] == "auth_tokens.middleware.TokenConsultancyMiddleware"

    def test_the_consultancy_status_is_applied_right_after_the_token_check(self) -> None:
        token = settings.MIDDLEWARE.index("auth_tokens.middleware.TokenConsultancyMiddleware")
        assert settings.MIDDLEWARE[token + 1] == "tenancy.middleware.ConsultancyAccessMiddleware"

    def test_no_session_or_csrf_middleware_is_left(self) -> None:
        assert not [name for name in settings.MIDDLEWARE if "session" in name or "csrf" in name]
        assert "django.contrib.sessions" not in settings.INSTALLED_APPS

    def test_the_blacklist_lives_in_every_schema(self) -> None:
        assert "auth_tokens" in settings.SHARED_APPS
        assert "auth_tokens" in settings.TENANT_APPS
