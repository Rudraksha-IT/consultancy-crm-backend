"""The Platform Admins tab (issue 65): list, invite by Code, resend, deactivate, and joining.

Everything is on the Platform host and for Platform Admins only. An invited Platform Admin joins
on the Platform's Join page with the emailed Code (Platform scope) and chooses a password; two-step
login then follows the usual rules.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django_tenants.utils import schema_context
from pytest_django.fixtures import Settings
from rest_framework import status
from rest_framework.test import APIClient

from codes.services import MAX_ATTEMPTS
from platform_admin.models import PlatformAdmin, PlatformAdminChange
from staff.models import Role
from tenancy.subdomains import get_central_login_host, get_platform_host
from tests.conftest import (
    PLATFORM_TEST_PASSWORD,
    PUBLIC_SCHEMA,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    use_access_token,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

ADMINS_URL = "/api/platform/admins/"
JOIN_URL = "/api/platform/auth/join/"
JOIN_VERIFY_URL = "/api/platform/auth/join/verify/"
JOIN_COMPLETE_URL = "/api/platform/auth/join/complete/"
LOGIN_URL = "/api/platform/auth/login/"
RESET_URL = "/api/platform/auth/password-reset/"
ME_URL = "/api/platform/me/"
NEW_EMAIL = "rita@platform.test"
NEW_NAME = "Rita Shrestha"
NEW_PASSWORD = "Kathmandu!2026"  # noqa: S105  # test-only credential
SENT_ANSWER = {"detail": "If that email belongs to someone here, we've sent them a 6-digit code."}
CODE_REFUSED = {
    "detail": "That code isn't right or has run out. Check the email, or send yourself a new one."
}
INJECTION_STRINGS = [
    "<script>alert(1)</script>",
    "'; DROP TABLE platform_admin_platformadmin; --",
    "}body{background:url(x)}",
]


@pytest.fixture
def me(platform_site: PlatformContext) -> PlatformAdmin:
    """The Platform Admin logged in on `platform_site.client`."""
    return platform_site.log_in()


def post(client: APIClient, url: str, body: object = None) -> _MonkeyPatchedResponse:
    """POST `body` as JSON."""
    return client.post(url, {} if body is None else body, format="json")


def invite(client: APIClient, name: str = NEW_NAME, email: str = NEW_EMAIL) -> Any:  # noqa: ANN401
    """Invite a Platform Admin and return the answer."""
    return post(client, ADMINS_URL, {"name": name, "email": email})


def emailed_code() -> str:
    """The 6-digit Code in the last email."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert found is not None
    return found.group(1)


def stored(email: str) -> PlatformAdmin:
    """The Platform Admin with `email` as saved now."""
    with schema_context(PUBLIC_SCHEMA):
        return PlatformAdmin.objects.get(email=email)


def row_of(client: APIClient, email: str) -> dict[str, Any]:
    """One Platform Admin's row in the list."""
    rows: list[dict[str, Any]] = client.get(ADMINS_URL).json()
    [row] = [r for r in rows if r["email"] == email]
    return row


def join(site: PlatformContext, code: str, password: object = NEW_PASSWORD) -> Any:  # noqa: ANN401
    """Finish joining with the Code and a password."""
    body = {"email": NEW_EMAIL, "code": code, "new_password": password}
    return post(site.new_client(), JOIN_COMPLETE_URL, body)


def log_in(site: PlatformContext, email: str, password: str) -> Any:  # noqa: ANN401
    """Log in on the Platform."""
    return post(site.new_client(), LOGIN_URL, {"email": email, "password": password})


class TestList:
    def test_lists_every_platform_admin_with_their_status(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)

        rows = platform_site.client.get(ADMINS_URL).json()

        assert rows == [
            {
                "id": str(me.public_id),
                "name": me.name,
                "email": me.email,
                "status": "active",
                "two_factor_required": False,
                "two_factor_set_up": False,
                "is_me": True,
            },
            {
                "id": str(stored(NEW_EMAIL).public_id),
                "name": NEW_NAME,
                "email": NEW_EMAIL,
                "status": "invited",
                "two_factor_required": False,
                "two_factor_set_up": False,
                "is_me": False,
            },
        ]

    def test_never_shows_a_password_or_secret(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        text = platform_site.client.get(ADMINS_URL).content.decode()

        assert "password" not in text
        assert "pbkdf2" not in text
        assert "secret" not in text


class TestInvite:
    def test_adds_an_invited_platform_admin_and_emails_a_join_code(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        response = invite(platform_site.client)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["status"] == "invited"
        admin = stored(NEW_EMAIL)
        assert admin.name == NEW_NAME
        assert not admin.has_usable_password()
        assert admin.joined_at is None
        [email] = mail.outbox
        assert email.to == [NEW_EMAIL]
        assert "Platform" in email.subject
        assert f"{get_platform_host()}/platform/join" in str(email.body)
        assert re.search(r"\b\d{6}\b", str(email.body))
        with schema_context(PUBLIC_SCHEMA):
            change = PlatformAdminChange.objects.get(platform_admin=admin)
        assert change.created
        assert change.by_id == me.pk

    def test_two_step_login_starts_from_the_deploy_default(
        self, platform_site: PlatformContext, me: PlatformAdmin, settings: Settings
    ) -> None:
        settings.PLATFORM_ADMIN_TWO_FACTOR_DEFAULT = True

        invite(platform_site.client)

        assert stored(NEW_EMAIL).two_factor_required is True

    @pytest.mark.parametrize("email", ["ops@platform.test", "OPS@Platform.test"])
    def test_an_email_already_used_is_refused(
        self, platform_site: PlatformContext, me: PlatformAdmin, email: str
    ) -> None:
        response = invite(platform_site.client, email=email)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.json()
        assert mail.outbox == []

    def test_an_invited_person_cannot_log_in_or_reset_a_password(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)
        mail.outbox.clear()

        assert log_in(platform_site, NEW_EMAIL, NEW_PASSWORD).status_code == 400
        assert post(platform_site.new_client(), RESET_URL, {"email": NEW_EMAIL}).json() == (
            SENT_ANSWER
        )
        assert mail.outbox == []

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": NEW_NAME},
            {"email": NEW_EMAIL},
            {"name": NEW_NAME, "email": NEW_EMAIL, "password": NEW_PASSWORD},
            {"name": NEW_NAME, "email": NEW_EMAIL, "is_active": True},
            {"name": 7, "email": NEW_EMAIL},
            {"name": NEW_NAME, "email": "not-an-email"},
            {"name": "x" * 201, "email": NEW_EMAIL},
            {"name": NEW_NAME, "email": f"{'a' * 250}@platform.test"},
            {"name": "", "email": NEW_EMAIL},
            [],
        ],
    )
    def test_bad_input_adds_nobody(
        self, platform_site: PlatformContext, me: PlatformAdmin, body: object
    ) -> None:
        response = post(platform_site.client, ADMINS_URL, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        with schema_context(PUBLIC_SCHEMA):
            assert PlatformAdmin.objects.count() == 1
        assert mail.outbox == []

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_values_are_refused(
        self, platform_site: PlatformContext, me: PlatformAdmin, text: str
    ) -> None:
        assert invite(platform_site.client, name=text).status_code == 400
        assert invite(platform_site.client, email=text).status_code == 400

    def test_logged_with_ids_only(
        self, platform_site: PlatformContext, me: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        invite(platform_site.client)

        added = stored(NEW_EMAIL)
        assert json_logs.with_message(
            f"Platform Admin invited: platform_admin_id={added.pk}, by_platform_admin_id={me.pk}"
        )
        for secret in (NEW_EMAIL, NEW_NAME, emailed_code()):
            assert secret not in json_logs.text()


class TestJoin:
    def test_the_invited_person_joins_with_the_code_then_logs_in(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)
        code = emailed_code()

        checked = post(
            platform_site.new_client(), JOIN_VERIFY_URL, {"email": NEW_EMAIL, "code": code}
        )
        joined = join(platform_site, code)

        assert checked.status_code == status.HTTP_200_OK
        assert joined.status_code == status.HTTP_200_OK
        assert joined.json() == {"detail": "Your password is set. Log in with it."}
        assert "access" not in joined.json()
        assert stored(NEW_EMAIL).joined_at is not None
        assert log_in(platform_site, NEW_EMAIL, NEW_PASSWORD).status_code == 200
        assert row_of(platform_site.client, NEW_EMAIL)["status"] == "active"

    def test_two_step_login_follows_after_joining(
        self, platform_site: PlatformContext, me: PlatformAdmin, settings: Settings
    ) -> None:
        settings.PLATFORM_ADMIN_TWO_FACTOR_DEFAULT = True
        invite(platform_site.client)
        join(platform_site, emailed_code())

        response = log_in(platform_site, NEW_EMAIL, NEW_PASSWORD)

        assert response.json()["two_factor"] == "setup"
        assert "access" not in response.json()

    def test_send_me_a_new_code_answers_the_same_and_replaces_the_old_one(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)
        old = emailed_code()

        for email in (NEW_EMAIL, "nobody@platform.test", me.email):
            response = post(platform_site.new_client(), JOIN_URL, {"email": email})
            assert response.status_code == status.HTTP_202_ACCEPTED
            assert response.json() == SENT_ANSWER
        new = emailed_code()

        assert len(mail.outbox) == 2
        if new != old:
            assert join(platform_site, old).json() == CODE_REFUSED
        assert join(platform_site, new).status_code == status.HTTP_200_OK

    def test_a_weak_password_keeps_the_code(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)
        code = emailed_code()

        weak = join(platform_site, code, "weakpass")

        assert weak.status_code == status.HTTP_400_BAD_REQUEST
        assert weak.json()["unmet_rules"] == ["capital", "number", "symbol"]
        assert join(platform_site, code).status_code == status.HTTP_200_OK

    def test_wrong_used_and_out_of_tries_codes_are_refused(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)
        code = emailed_code()
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"

        for _ in range(MAX_ATTEMPTS):
            assert join(platform_site, wrong).json() == CODE_REFUSED
        assert join(platform_site, code).json() == CODE_REFUSED

    def test_only_an_invited_person_can_join(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        body = {"email": me.email, "code": "123456", "new_password": NEW_PASSWORD}

        assert post(platform_site.new_client(), JOIN_COMPLETE_URL, body).json() == CODE_REFUSED
        assert stored(me.email).check_password(PLATFORM_TEST_PASSWORD)

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": NEW_EMAIL, "code": "12345"},
            {"email": NEW_EMAIL, "code": "abcdef"},
            {"email": NEW_EMAIL, "code": "123456", "new_password": NEW_PASSWORD, "x": 1},
            {"email": "<script>", "code": "123456", "new_password": NEW_PASSWORD},
            {"email": NEW_EMAIL, "code": "123456", "new_password": "A1!" + "a" * 126},
        ],
    )
    def test_bad_join_input_is_refused(
        self, platform_site: PlatformContext, me: PlatformAdmin, body: dict[str, object]
    ) -> None:
        assert post(platform_site.new_client(), JOIN_COMPLETE_URL, body).status_code == 400

    def test_the_join_endpoints_exist_only_on_the_platform_host(
        self,
        platform_site: PlatformContext,
        me: PlatformAdmin,
        make_consultancy: ConsultancyFactory,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        for client in (abc.new_client(), api_client(get_central_login_host())):
            for url in (JOIN_URL, JOIN_VERIFY_URL, JOIN_COMPLETE_URL, ADMINS_URL):
                assert post(client, url, {"email": NEW_EMAIL}).status_code == 404


class TestResend:
    def test_emails_a_new_code_to_someone_still_invited(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)
        admin = stored(NEW_EMAIL)

        response = post(platform_site.client, f"{ADMINS_URL}{admin.public_id}/resend/")

        assert response.status_code == status.HTTP_200_OK
        assert len(mail.outbox) == 2

    def test_refused_for_someone_who_has_joined(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        response = post(platform_site.client, f"{ADMINS_URL}{me.public_id}/resend/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert mail.outbox == []

    def test_unknown_is_not_found(self, platform_site: PlatformContext, me: PlatformAdmin) -> None:
        assert post(platform_site.client, f"{ADMINS_URL}{uuid.uuid4()}/resend/").status_code == 404


class TestDeactivate:
    def test_deactivates_and_signs_out_another_platform_admin(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        other = platform_site.add_admin(email="other@platform.test")
        session = platform_site.new_client()
        use_access_token(session, platform_site.tokens_for(other).access)

        response = post(platform_site.client, f"{ADMINS_URL}{other.public_id}/deactivate/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "deactivated"
        assert session.get(ME_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert log_in(platform_site, other.email, PLATFORM_TEST_PASSWORD).status_code == 400
        with schema_context(PUBLIC_SCHEMA):
            change = PlatformAdminChange.objects.filter(platform_admin=other).last()
        assert change is not None
        assert (change.fields, change.by_id) == (["is_active"], me.pk)

    def test_deactivating_an_invite_stops_its_code(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)
        code = emailed_code()

        post(platform_site.client, f"{ADMINS_URL}{stored(NEW_EMAIL).public_id}/deactivate/")

        assert join(platform_site, code).json() == CODE_REFUSED

    def test_never_yourself(self, platform_site: PlatformContext, me: PlatformAdmin) -> None:
        platform_site.add_admin(email="other@platform.test")

        response = post(platform_site.client, f"{ADMINS_URL}{me.public_id}/deactivate/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "You can't deactivate yourself."}
        assert stored(me.email).is_active

    def test_never_the_last_active_one(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        other = platform_site.add_admin(email="other@platform.test")
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=me.pk).update(is_active=False)
        client = platform_site.new_client()
        use_access_token(client, platform_site.tokens_for(other).access)

        response = post(client, f"{ADMINS_URL}{other.public_id}/deactivate/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(other.email).is_active

    def test_already_deactivated_is_refused(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        other = platform_site.add_admin(email="other@platform.test")
        post(platform_site.client, f"{ADMINS_URL}{other.public_id}/deactivate/")

        response = post(platform_site.client, f"{ADMINS_URL}{other.public_id}/deactivate/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestWhoCan:
    def test_nobody_logged_in_is_refused(self, platform_site: PlatformContext) -> None:
        client = platform_site.new_client()

        assert client.get(ADMINS_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert invite(client).status_code == status.HTTP_401_UNAUTHORIZED

    def test_staff_tokens_are_refused_everywhere(
        self, platform_site: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        access = abc.tokens_for(abc.add_staff(Role.ADMIN)).access
        for client in (abc.new_client(), platform_site.new_client()):
            use_access_token(client, access)
            assert client.get(ADMINS_URL).status_code in {401, 403, 404}
            assert invite(client).status_code in {401, 403, 404}
        with schema_context(PUBLIC_SCHEMA):
            assert not PlatformAdmin.objects.filter(email=NEW_EMAIL).exists()

    def test_only_the_methods_needed(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        client = platform_site.client
        assert client.put(ADMINS_URL, {}, format="json").status_code == 405
        assert client.delete(ADMINS_URL).status_code == 405
        assert client.get(f"{ADMINS_URL}{me.public_id}/deactivate/").status_code == 405
        assert client.get(JOIN_COMPLETE_URL).status_code == 405


class TestReactivate:
    """Bring a deactivated Platform Admin back (issue 65): no new account, no lost history."""

    def test_someone_who_had_joined_is_active_again_and_can_log_in(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        other = platform_site.add_admin(email="other@platform.test")
        post(platform_site.client, f"{ADMINS_URL}{other.public_id}/deactivate/")

        response = post(platform_site.client, f"{ADMINS_URL}{other.public_id}/reactivate/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "active"
        assert log_in(platform_site, other.email, PLATFORM_TEST_PASSWORD).status_code == 200
        with schema_context(PUBLIC_SCHEMA):
            change = PlatformAdminChange.objects.filter(platform_admin=other).last()
        assert change is not None
        assert (change.fields, change.by_id) == (["is_active"], me.pk)
        assert mail.outbox == []

    def test_a_cancelled_invite_is_invited_again_with_a_new_code(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        invite(platform_site.client)
        old = emailed_code()
        rita = stored(NEW_EMAIL)
        post(platform_site.client, f"{ADMINS_URL}{rita.public_id}/deactivate/")

        response = post(platform_site.client, f"{ADMINS_URL}{rita.public_id}/reactivate/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "invited"
        assert len(mail.outbox) == 2
        new = emailed_code()
        if new != old:
            assert join(platform_site, old).json() == CODE_REFUSED
        assert join(platform_site, new).status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("who", ["me", "active", "invited"])
    def test_only_someone_deactivated_can_be_reactivated(
        self, platform_site: PlatformContext, me: PlatformAdmin, who: str
    ) -> None:
        if who == "me":
            target = me.public_id
        elif who == "active":
            target = platform_site.add_admin(email="other@platform.test").public_id
        else:
            invite(platform_site.client)
            target = stored(NEW_EMAIL).public_id
        mail.outbox.clear()

        response = post(platform_site.client, f"{ADMINS_URL}{target}/reactivate/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "detail": "Only a deactivated Platform Admin can be reactivated."
        }
        assert mail.outbox == []

    def test_unknown_is_not_found_and_only_post(
        self, platform_site: PlatformContext, me: PlatformAdmin
    ) -> None:
        assert (
            post(platform_site.client, f"{ADMINS_URL}{uuid.uuid4()}/reactivate/").status_code == 404
        )
        assert (
            platform_site.client.get(f"{ADMINS_URL}{me.public_id}/reactivate/").status_code == 405
        )

    def test_nobody_logged_in_and_staff_are_refused(
        self, platform_site: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        admin = platform_site.add_admin(email="other@platform.test")
        with schema_context(PUBLIC_SCHEMA):
            PlatformAdmin.objects.filter(pk=admin.pk).update(is_active=False)
        abc = make_consultancy("ABC Education", "abcedu")
        access = abc.tokens_for(abc.add_staff(Role.ADMIN)).access
        staff_on_platform = platform_site.new_client()
        use_access_token(staff_on_platform, access)

        for client in (platform_site.new_client(), staff_on_platform, abc.client):
            assert post(client, f"{ADMINS_URL}{admin.public_id}/reactivate/").status_code in {
                401,
                403,
                404,
            }
        assert not stored(admin.email).is_active

    def test_logged_with_ids_only(
        self, platform_site: PlatformContext, me: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        other = platform_site.add_admin(email="other@platform.test")
        post(platform_site.client, f"{ADMINS_URL}{other.public_id}/deactivate/")

        post(platform_site.client, f"{ADMINS_URL}{other.public_id}/reactivate/")

        assert json_logs.with_message(
            f"Platform Admin reactivated: platform_admin_id={other.pk}, "
            f"by_platform_admin_id={me.pk}"
        )
        assert "other@platform.test" not in json_logs.text()
