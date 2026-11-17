"""Two-step login with an authenticator app, for the Platform Admin (issue 61).

After the password, the Platform Admin enters the 6-digit number from their app (TOTP), or a
recovery code. No login token is given out until both steps pass.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pyotp
import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.db import connection
from django_tenants.utils import schema_context
from freezegun import freeze_time
from freezegun.api import FrozenDateTimeFactory
from pytest_django.fixtures import Settings
from rest_framework import status
from rest_framework.test import APIClient

from platform_admin.models import PlatformAdmin
from staff.models import Role
from tenancy.subdomains import get_central_login_host
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    PUBLIC_SCHEMA,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    use_access_token,
)
from two_factor.models import EventKind, RecoveryCode, TwoFactorAccount, TwoFactorEvent
from two_factor.services import (
    CHALLENGE_TRIES_ALLOWED,
    ISSUER,
    PERSON_WRONG_NUMBERS_ALLOWED,
)

LOGIN_URL = "/api/platform/auth/login/"
SETUP_URL = "/api/platform/auth/two-factor/setup/"
CONFIRM_URL = "/api/platform/auth/two-factor/confirm/"
VERIFY_URL = "/api/platform/auth/two-factor/verify/"
MINE_URL = "/api/platform/me/two-factor/"
ME_URL = "/api/platform/me/"
PASSWORD_URL = "/api/platform/me/password/"  # noqa: S105  # a path, not a secret
CENTRAL_HOST = get_central_login_host()
NOW = datetime(2026, 11, 6, 9, 0, 0, tzinfo=UTC)
STEP = timedelta(seconds=30)

INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE two_factor_twofactoraccount;--",
    "1 OR 1=1",
    "x{color:red}",
]


@pytest.fixture
def frozen() -> Iterator[FrozenDateTimeFactory]:
    """Stop the clock at NOW; tests move it on with `tick`."""
    with freeze_time(NOW) as clock:
        assert isinstance(clock, FrozenDateTimeFactory)
        yield clock


def require_two_factor(admin: PlatformAdmin) -> None:
    """Switch two-step login on for `admin`, as the Security page would."""
    with schema_context(PUBLIC_SCHEMA):
        PlatformAdmin.objects.filter(pk=admin.pk).update(two_factor_required=True)


def stored(admin: PlatformAdmin) -> PlatformAdmin:
    """The Platform Admin as saved now."""
    with schema_context(PUBLIC_SCHEMA):
        return PlatformAdmin.objects.get(pk=admin.pk)


def log_in(client: APIClient, admin: PlatformAdmin) -> dict[str, Any]:
    """Log in with the password and return the answer."""
    response = client.post(
        LOGIN_URL, {"email": admin.email, "password": PLATFORM_TEST_PASSWORD}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    body: dict[str, Any] = response.json()
    return body


def secret_of(uri: str) -> str:
    """The secret inside a setup URI."""
    return parse_qs(urlparse(uri).query)["secret"][0]


def number(secret: str, at: datetime) -> str:
    """The number the app shows at `at`."""
    return pyotp.TOTP(secret).at(at)


def wrong(secret: str, at: datetime) -> str:
    """A number that is wrong at `at`, one step either side too."""
    near = {number(secret, at + n * STEP) for n in (-1, 0, 1)}
    return next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in near)


def set_up(client: APIClient, admin: PlatformAdmin) -> tuple[str, list[str]]:
    """Switch two-step login on for `admin`, set it up, and return the secret and codes."""
    require_two_factor(admin)
    challenge = log_in(client, admin)["challenge"]
    uri = client.post(SETUP_URL, {"challenge": challenge}, format="json").json()["uri"]
    secret = secret_of(uri)
    confirm = client.post(
        CONFIRM_URL,
        {"challenge": challenge, "code": number(secret, datetime.now(tz=UTC))},
        format="json",
    )
    assert confirm.status_code == status.HTTP_200_OK, confirm.json()
    return secret, confirm.json()["recovery_codes"]


def verify(client: APIClient, admin: PlatformAdmin, **answer: str) -> Any:  # noqa: ANN401
    """Log in with the password, then send `answer` as the second step."""
    challenge = log_in(client, admin)["challenge"]
    return client.post(VERIFY_URL, {"challenge": challenge, **answer}, format="json")


def logged_in_client(platform_site: PlatformContext, admin: PlatformAdmin) -> APIClient:
    """A new client logged in as `admin` (a second device)."""
    client = platform_site.new_client()
    # As saved now: setting up raised its auth_version.
    use_access_token(client, platform_site.tokens_for(stored(admin)).access)
    return client


def events_of(admin: PlatformAdmin) -> list[str]:
    """The two-step login records of `admin`, oldest first."""
    with schema_context(PUBLIC_SCHEMA):
        return list(
            TwoFactorEvent.objects.filter(person_id=admin.pk)
            .order_by("pk")
            .values_list("what", flat=True)
        )


class TestLogin:
    def test_switched_off_the_password_alone_logs_in(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()

        body = log_in(platform_site.client, admin)

        assert "access" in body
        assert "two_factor" not in body

    def test_switched_on_and_not_set_up_setup_is_forced(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        require_two_factor(admin)

        body = log_in(platform_site.client, admin)

        assert body["two_factor"] == "setup"
        assert set(body) == {"two_factor", "challenge"}

    def test_switched_on_and_set_up_a_number_is_needed(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        frozen.tick(STEP)

        body = log_in(platform_site.client, admin)
        response = platform_site.client.post(
            VERIFY_URL,
            {"challenge": body["challenge"], "code": number(secret, NOW + STEP)},
            format="json",
        )

        assert body["two_factor"] == "verify"
        assert "access" not in body
        assert response.status_code == status.HTTP_200_OK
        use_access_token(platform_site.client, response.json()["access"])
        assert platform_site.client.get(ME_URL).status_code == status.HTTP_200_OK

    def test_the_challenge_is_not_a_login_token(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        require_two_factor(admin)
        challenge = log_in(platform_site.client, admin)["challenge"]

        use_access_token(platform_site.client, challenge)

        assert platform_site.client.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_wrong_password_never_starts_a_challenge(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        require_two_factor(admin)

        response = platform_site.client.post(
            LOGIN_URL, {"email": admin.email, "password": "Wrong-Pass-1!"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "challenge" not in response.json()


class TestSetup:
    def test_setup_gives_the_uri_once_for_this_admin(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        require_two_factor(admin)
        challenge = log_in(platform_site.client, admin)["challenge"]

        first = platform_site.client.post(SETUP_URL, {"challenge": challenge}, format="json")
        again = platform_site.client.post(SETUP_URL, {"challenge": challenge}, format="json")

        uri = urlparse(first.json()["uri"])
        assert first.status_code == status.HTTP_200_OK
        assert uri.scheme == "otpauth"
        assert admin.email in unquote(uri.path)
        assert parse_qs(uri.query)["issuer"] == [ISSUER]
        assert again.status_code == status.HTTP_400_BAD_REQUEST
        assert again.json()["start_again"] is True

    def test_confirm_gives_tokens_and_ten_recovery_codes(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()

        _, codes = set_up(platform_site.client, admin)

        assert len(codes) == 10
        assert events_of(admin) == [EventKind.SET_UP]

    def test_a_wrong_number_doesnt_set_it_up(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        require_two_factor(admin)
        challenge = log_in(platform_site.client, admin)["challenge"]
        uri = platform_site.client.post(SETUP_URL, {"challenge": challenge}, format="json")
        secret = secret_of(uri.json()["uri"])

        response = platform_site.client.post(
            CONFIRM_URL,
            {"challenge": challenge, "code": wrong(secret, datetime.now(tz=UTC))},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "code" in response.json()
        assert "access" not in response.json()
        with schema_context(PUBLIC_SCHEMA):
            assert not TwoFactorAccount.objects.filter(person_id=admin.pk).exclude(secret="")

    def test_confirm_needs_setup_first(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        require_two_factor(admin)
        challenge = log_in(platform_site.client, admin)["challenge"]

        response = platform_site.client.post(
            CONFIRM_URL, {"challenge": challenge, "code": "123456"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["start_again"] is True

    def test_a_verify_challenge_cant_set_up_again(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        set_up(platform_site.new_client(), admin)
        challenge = log_in(platform_site.client, admin)["challenge"]

        response = platform_site.client.post(SETUP_URL, {"challenge": challenge}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["start_again"] is True

    def test_setting_up_signs_out_everywhere_else(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        other = logged_in_client(platform_site, admin)

        set_up(platform_site.client, admin)

        assert other.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED


class TestNumbers:
    def test_a_wrong_number_is_refused(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)

        response = verify(platform_site.client, admin, code=wrong(secret, NOW))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "code" in response.json()
        assert "access" not in response.json()

    def test_a_replayed_number_is_refused(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)

        # The number that confirmed the setup was just used.
        response = verify(platform_site.client, admin, code=number(secret, NOW))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_number_is_used_only_once(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        later = number(secret, NOW + STEP)

        first = verify(platform_site.client, admin, code=later)
        second = verify(platform_site.new_client(), admin, code=later)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_number_outside_the_window_is_refused(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        frozen.tick(4 * STEP)

        response = verify(platform_site.client, admin, code=number(secret, NOW + STEP))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_sixth_try_after_five_wrong_ones_ends_the_challenge(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        challenge = log_in(platform_site.client, admin)["challenge"]
        bad = {"challenge": challenge, "code": wrong(secret, NOW)}
        for _ in range(CHALLENGE_TRIES_ALLOWED):
            refused = platform_site.client.post(VERIFY_URL, bad, format="json")
            assert refused.status_code == status.HTTP_400_BAD_REQUEST
            assert "code" in refused.json()

        sixth = platform_site.client.post(
            VERIFY_URL,
            {"challenge": challenge, "code": number(secret, NOW + STEP)},
            format="json",
        )

        assert CHALLENGE_TRIES_ALLOWED == 5
        assert sixth.status_code == status.HTTP_400_BAD_REQUEST
        assert sixth.json()["start_again"] is True

    def test_too_many_wrong_numbers_across_challenges_waits(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        for _ in range(PERSON_WRONG_NUMBERS_ALLOWED):
            verify(platform_site.client, admin, code=wrong(secret, NOW))

        response = verify(platform_site.client, admin, code=number(secret, NOW + STEP))

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        frozen.tick(timedelta(minutes=16))
        later = verify(platform_site.client, admin, code=number(secret, NOW + 32 * STEP + STEP))
        assert later.status_code == status.HTTP_200_OK

    def test_an_expired_challenge_is_refused(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        challenge = log_in(platform_site.client, admin)["challenge"]
        frozen.tick(timedelta(minutes=5, seconds=1))

        response = platform_site.client.post(
            VERIFY_URL,
            {"challenge": challenge, "code": number(secret, NOW + 11 * STEP)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["start_again"] is True

    def test_a_challenge_is_used_once(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        challenge = log_in(platform_site.client, admin)["challenge"]
        first = platform_site.client.post(
            VERIFY_URL,
            {"challenge": challenge, "code": number(secret, NOW + STEP)},
            format="json",
        )
        frozen.tick(STEP)

        again = platform_site.client.post(
            VERIFY_URL,
            {"challenge": challenge, "code": number(secret, NOW + 2 * STEP)},
            format="json",
        )

        assert first.status_code == status.HTTP_200_OK
        assert again.status_code == status.HTTP_400_BAD_REQUEST
        assert again.json()["start_again"] is True

    @pytest.mark.parametrize("challenge", ["", "garbage", "a.b.c", "x" * 600])
    def test_a_forged_challenge_is_refused(
        self, platform_site: PlatformContext, challenge: str
    ) -> None:
        response = platform_site.client.post(
            VERIFY_URL, {"challenge": challenge, "code": "123456"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "access" not in response.json()


class TestRecoveryCodes:
    def test_each_works_once(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        _, codes = set_up(platform_site.new_client(), admin)

        first = verify(platform_site.client, admin, recovery_code=codes[0])
        again = verify(platform_site.new_client(), admin, recovery_code=codes[0])
        other = verify(platform_site.new_client(), admin, recovery_code=codes[1].upper())

        assert first.status_code == status.HTTP_200_OK
        assert again.status_code == status.HTTP_400_BAD_REQUEST
        assert "recovery_code" in again.json()
        assert other.status_code == status.HTTP_200_OK

    def test_using_one_is_recorded_and_signs_out_everywhere_else(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        _, codes = set_up(platform_site.new_client(), admin)
        other = logged_in_client(platform_site, admin)

        response = verify(platform_site.client, admin, recovery_code=codes[0])

        assert response.status_code == status.HTTP_200_OK
        assert other.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert events_of(admin)[-1] == EventKind.RECOVERY_CODE_USED

    def test_codes_are_stored_as_hashes_only(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        _, codes = set_up(platform_site.new_client(), admin)

        with schema_context(PUBLIC_SCHEMA):
            hashes = list(RecoveryCode.objects.values_list("code_hash", flat=True))
        assert len(hashes) == 10
        assert not set(hashes) & set(codes)
        assert all(code.replace("-", "") not in "".join(hashes) for code in codes)

    def test_a_code_and_a_number_together_are_refused(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        _, codes = set_up(platform_site.new_client(), admin)

        response = verify(platform_site.client, admin, code="123456", recovery_code=codes[0])

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestChallengesStayOnTheirHost:
    def test_a_platform_challenge_is_refused_on_an_office_host(
        self, platform_site: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        office = make_consultancy("Acme", "acme")
        admin = platform_site.add_admin()
        _, codes = set_up(platform_site.new_client(), admin)
        challenge = log_in(platform_site.client, admin)["challenge"]

        response = office.client.post(
            VERIFY_URL, {"challenge": challenge, "recovery_code": codes[0]}, format="json"
        )

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_404_NOT_FOUND}
        assert "access" not in response.content.decode()

    def test_a_platform_challenge_is_refused_on_the_central_login_host(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        _, codes = set_up(platform_site.new_client(), admin)
        challenge = log_in(platform_site.client, admin)["challenge"]

        response = api_client(CENTRAL_HOST).post(
            VERIFY_URL, {"challenge": challenge, "recovery_code": codes[0]}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_service_refuses_a_challenge_from_another_host_and_warns(
        self, platform_site: PlatformContext, json_logs: JsonLogs
    ) -> None:
        from two_factor.exceptions import ChallengeEndedError  # noqa: PLC0415
        from two_factor.services import verify_with_recovery_code  # noqa: PLC0415

        admin = platform_site.add_admin()
        _, codes = set_up(platform_site.new_client(), admin)
        challenge = log_in(platform_site.client, admin)["challenge"]

        with schema_context(PUBLIC_SCHEMA), pytest.raises(ChallengeEndedError):
            verify_with_recovery_code(challenge, codes[0], host=CENTRAL_HOST)

        assert json_logs.with_message("two-step challenge used on the wrong host")
        response = platform_site.client.post(
            VERIFY_URL, {"challenge": challenge, "recovery_code": codes[0]}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK


class TestMyTwoFactor:
    def test_shows_the_state(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = platform_site.client.get(MINE_URL)

        assert response.json() == {"required": False, "set_up": False, "recovery_codes_left": 0}

    def test_switching_on_starts_setup_at_once_and_signs_out_everywhere(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.log_in()
        other = logged_in_client(platform_site, admin)

        response = platform_site.client.post(MINE_URL, {"action": "on"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["two_factor"] == "setup"
        assert "access" not in response.json()
        assert stored(admin).two_factor_required
        assert other.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert platform_site.client.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert events_of(admin) == [EventKind.SWITCHED_ON]
        uri = platform_site.new_client().post(
            SETUP_URL, {"challenge": response.json()["challenge"]}, format="json"
        )
        assert uri.status_code == status.HTTP_200_OK

    def test_switching_on_twice_is_refused(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)

        response = client.post(MINE_URL, {"action": "on"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_switching_on_needs_the_secrets_key(
        self, platform_site: PlatformContext, settings: Settings
    ) -> None:
        admin = platform_site.log_in()
        settings.TWO_FACTOR_SECRETS_KEY = ""

        response = platform_site.client.post(MINE_URL, {"action": "on"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not stored(admin).two_factor_required

    def test_switching_off_with_password_and_number(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)
        other = logged_in_client(platform_site, admin)

        response = client.post(
            MINE_URL,
            {
                "action": "off",
                "password": PLATFORM_TEST_PASSWORD,
                "code": number(secret, NOW + STEP),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert set(response.json()) == {"access", "refresh", "two_factor"}
        assert response.json()["two_factor"]["required"] is False
        assert not stored(admin).two_factor_required
        assert other.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert events_of(admin)[-1] == EventKind.SWITCHED_OFF
        with schema_context(PUBLIC_SCHEMA):
            assert not RecoveryCode.objects.exists()
            assert not TwoFactorAccount.objects.exclude(secret="").exists()
        assert "access" in log_in(platform_site.new_client(), admin)

    def test_switching_off_with_a_recovery_code(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        _, codes = set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)

        response = client.post(
            MINE_URL,
            {"action": "off", "password": PLATFORM_TEST_PASSWORD, "recovery_code": codes[3]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("missing", ["password", "code"])
    def test_switching_off_without_the_password_or_a_number_is_refused(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
        missing: str,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)
        body = {"action": "off", "password": PLATFORM_TEST_PASSWORD, "code": number(secret, NOW)}
        del body[missing]

        response = client.post(MINE_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(admin).two_factor_required

    def test_switching_off_with_a_wrong_password_or_number_is_refused(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)

        bad_password = client.post(
            MINE_URL,
            {"action": "off", "password": "Wrong-Pass-1!", "code": number(secret, NOW + STEP)},
            format="json",
        )
        bad_number = client.post(
            MINE_URL,
            {"action": "off", "password": PLATFORM_TEST_PASSWORD, "code": wrong(secret, NOW)},
            format="json",
        )

        assert bad_password.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in bad_password.json()
        assert bad_number.status_code == status.HTTP_400_BAD_REQUEST
        assert "code" in bad_number.json()
        assert stored(admin).two_factor_required

    def test_making_new_recovery_codes_replaces_the_old_ones(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, old = set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)

        response = client.post(
            MINE_URL,
            {"action": "new_recovery_codes", "code": number(secret, NOW + STEP)},
            format="json",
        )

        new = response.json()["recovery_codes"]
        assert response.status_code == status.HTTP_200_OK
        assert len(new) == 10
        assert not set(new) & set(old)
        assert verify(platform_site.new_client(), admin, recovery_code=old[0]).status_code == 400
        assert verify(platform_site.new_client(), admin, recovery_code=new[0]).status_code == 200
        state = logged_in_client(platform_site, admin).get(MINE_URL).json()
        assert state["recovery_codes_left"] == 9

    def test_new_recovery_codes_need_a_number(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)

        response = client.post(MINE_URL, {"action": "new_recovery_codes"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_only_a_platform_admin(
        self, platform_site: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        office = make_consultancy("Acme", "acme")
        office.log_in_as(Role.ADMIN)

        assert platform_site.client.get(MINE_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert office.client.get(MINE_URL).status_code == status.HTTP_404_NOT_FOUND


class TestAccountChangesAndTheNumber:
    def test_a_name_change_needs_no_number_even_once_set_up(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)

        response = client.patch(ME_URL, {"name": "New Name"}, format="json")

        # Issue 65: the name is free to change; only the password still needs the number.
        assert response.status_code == status.HTTP_200_OK

    def test_a_password_change_needs_the_number_once_set_up(
        self,
        platform_site: PlatformContext,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)
        client = logged_in_client(platform_site, admin)
        change = {"current_password": PLATFORM_TEST_PASSWORD, "new_password": "Fresh-Start-2027!"}

        refused = client.post(PASSWORD_URL, {**change, "code": wrong(secret, NOW)}, format="json")
        accepted = client.post(
            PASSWORD_URL, {**change, "code": number(secret, NOW + STEP)}, format="json"
        )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert accepted.status_code == status.HTTP_200_OK

    def test_without_two_step_login_no_number_is_asked(
        self, platform_site: PlatformContext
    ) -> None:
        platform_site.log_in()

        response = platform_site.client.patch(ME_URL, {"name": "New Name"}, format="json")

        assert response.status_code == status.HTTP_200_OK


class TestRefusedInput:
    @pytest.mark.parametrize(
        "code", ["12345a", "1234567", "12345", "", 123456, None, ["123456"], *INJECTION_STRINGS]
    )
    def test_a_code_that_isnt_six_digits(
        self, platform_site: PlatformContext, code: object
    ) -> None:
        admin = platform_site.add_admin()
        set_up(platform_site.new_client(), admin)

        response = verify(platform_site.client, admin, code=code)  # type: ignore[arg-type]

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "code" in response.json()
        for value in INJECTION_STRINGS:
            assert value not in response.content.decode()

    @pytest.mark.parametrize("url", [SETUP_URL, CONFIRM_URL, VERIFY_URL])
    def test_missing_and_unknown_fields(self, platform_site: PlatformContext, url: str) -> None:
        admin = platform_site.add_admin()
        require_two_factor(admin)
        challenge = log_in(platform_site.client, admin)["challenge"]

        missing = platform_site.client.post(url, {}, format="json")
        unknown = platform_site.client.post(
            url, {"challenge": challenge, "code": "123456", "admin": True}, format="json"
        )

        assert missing.status_code == status.HTTP_400_BAD_REQUEST
        assert unknown.status_code == status.HTTP_400_BAD_REQUEST
        assert "admin" not in unknown.content.decode()

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"action": "sideways"},
            {"action": "on", "extra": 1},
            {"action": ["on"]},
            {"action": "off", "password": 5, "code": "123456"},
            {"action": "<script>alert(1)</script>"},
        ],
    )
    def test_my_two_factor_refuses_bad_bodies(
        self, platform_site: PlatformContext, body: dict[str, object]
    ) -> None:
        admin = platform_site.log_in()

        response = platform_site.client.post(MINE_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not stored(admin).two_factor_required
        assert "<script>" not in response.content.decode()

    def test_form_bodies_are_refused(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        response = platform_site.client.post(MINE_URL, {"action": "on"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("url", [SETUP_URL, CONFIRM_URL, VERIFY_URL])
    def test_only_post(self, platform_site: PlatformContext, url: str) -> None:
        for method in ("get", "put", "patch", "delete"):
            response = getattr(platform_site.client, method)(url)
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_my_two_factor_is_get_and_post_only(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        for method in ("put", "patch", "delete"):
            response = getattr(platform_site.client, method)(MINE_URL, {}, format="json")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestSecretsStayHidden:
    def test_the_secret_is_encrypted_in_the_database(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)

        with connection.cursor() as cursor:
            cursor.execute("SELECT secret FROM public.two_factor_twofactoraccount")
            raw = cursor.fetchone()[0]
        assert raw
        assert secret not in raw

    def test_nothing_secret_reaches_the_logs_or_later_answers(
        self,
        platform_site: PlatformContext,
        json_logs: JsonLogs,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, codes = set_up(platform_site.new_client(), admin)
        challenge = log_in(platform_site.client, admin)["challenge"]
        wrong_number = wrong(secret, NOW)
        platform_site.client.post(
            VERIFY_URL, {"challenge": challenge, "code": wrong_number}, format="json"
        )
        good = platform_site.client.post(
            VERIFY_URL, {"challenge": challenge, "recovery_code": codes[0]}, format="json"
        )
        use_access_token(platform_site.client, good.json()["access"])
        state = platform_site.client.get(MINE_URL).content.decode()

        logged = json_logs.text()
        for value in (secret, challenge, wrong_number, number(secret, NOW), *codes):
            assert value not in logged
            assert value not in state
        assert json_logs.with_message("two-step login set up")
        assert json_logs.with_message("two-step number refused")
        assert json_logs.with_message("recovery code used")
        assert json_logs.with_message("two-step login passed")

    def test_a_replay_is_logged_as_a_warning(
        self,
        platform_site: PlatformContext,
        json_logs: JsonLogs,
        frozen: FrozenDateTimeFactory,
    ) -> None:
        admin = platform_site.add_admin()
        secret, _ = set_up(platform_site.new_client(), admin)

        verify(platform_site.client, admin, code=number(secret, NOW))

        assert (
            json_logs.only("two-step number replayed: kind=platform_admin id=" + str(admin.pk))[
                "level"
            ]
            == "WARNING"
        )


class TestTheEnvDefault:
    def _deploy(self, monkeypatch: pytest.MonkeyPatch, email: str, default: str) -> None:
        monkeypatch.setenv("PLATFORM_ADMIN_NAME", "Platform Ops")
        monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", email)
        monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "Deploy-Secret-2026!")
        monkeypatch.setenv("PLATFORM_ADMIN_TWO_FACTOR_DEFAULT", default)
        call_command("ensure_platform_admin", stdout=io.StringIO(), stderr=io.StringIO())

    def test_a_new_platform_admin_takes_the_default(
        self,
        platform_site: PlatformContext,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
    ) -> None:
        settings.PLATFORM_ADMIN_TWO_FACTOR_DEFAULT = True
        self._deploy(monkeypatch, "on@platform.test", "true")
        settings.PLATFORM_ADMIN_TWO_FACTOR_DEFAULT = False
        self._deploy(monkeypatch, "off@platform.test", "false")

        with schema_context(PUBLIC_SCHEMA):
            assert PlatformAdmin.objects.get(email="on@platform.test").two_factor_required
            assert not PlatformAdmin.objects.get(email="off@platform.test").two_factor_required

    def test_the_default_is_used_only_at_creation(
        self,
        platform_site: PlatformContext,
        monkeypatch: pytest.MonkeyPatch,
        settings: Settings,
    ) -> None:
        settings.PLATFORM_ADMIN_TWO_FACTOR_DEFAULT = False
        self._deploy(monkeypatch, "ops@platform.test", "false")
        settings.PLATFORM_ADMIN_TWO_FACTOR_DEFAULT = True
        self._deploy(monkeypatch, "ops@platform.test", "true")

        with schema_context(PUBLIC_SCHEMA):
            assert not PlatformAdmin.objects.get(email="ops@platform.test").two_factor_required


def test_a_fernet_key_is_used(settings: Settings) -> None:
    """The tests seal secrets with a real Fernet key."""
    assert Fernet(settings.TWO_FACTOR_SECRETS_KEY.encode())
