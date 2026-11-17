"""Platform Admins come from the deploy's PLATFORM_ADMIN_* values (issue 59).

`ensure_platform_admin` runs on every deploy. It only ever creates, never changes, and always
exits 0 so the deploy carries on; each run logs exactly one outcome.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command, get_commands
from django.db import IntegrityError, transaction
from django_tenants.utils import schema_context
from rest_framework import status

from platform_admin.models import PlatformAdmin
from tests.conftest import PUBLIC_SCHEMA, JsonLogs, PlatformContext

PLATFORM_LOGIN_URL = "/api/platform/auth/login/"
COMMAND = "ensure_platform_admin"
NAME_VAR = "PLATFORM_ADMIN_NAME"
EMAIL_VAR = "PLATFORM_ADMIN_EMAIL"
PASSWORD_VAR = "PLATFORM_ADMIN_PASSWORD"  # noqa: S105  # the variable's name, not a secret
GOOD_NAME = "Platform Ops"
GOOD_EMAIL = "Ops@Platform.test"
GOOD_PASSWORD = "Deploy-Secret-2026!"  # noqa: S105  # test-only credential
OTHER_PASSWORD = "Another-Secret-2027?"  # noqa: S105  # test-only credential

NOT_SET = "platform admin values not set; skipped"
INVALID = "platform admin values incomplete or invalid; skipped: "
EXISTS = "platform admin already exists; skipped: id="
CREATED = "platform admin created: id="
OUTCOMES = (NOT_SET, INVALID, EXISTS, CREATED)


def set_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str | None = GOOD_NAME,
    email: str | None = GOOD_EMAIL,
    password: str | None = GOOD_PASSWORD,
) -> None:
    """Set the deploy values; None leaves that variable out."""
    for var, value in ((NAME_VAR, name), (EMAIL_VAR, email), (PASSWORD_VAR, password)):
        if value is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, value)


def run() -> str:
    """Run the command as the deploy does and return what it printed."""
    out = io.StringIO()
    err = io.StringIO()
    call_command(COMMAND, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def admins() -> list[PlatformAdmin]:
    """Every Platform Admin, oldest first."""
    with schema_context(PUBLIC_SCHEMA):
        return list(PlatformAdmin.objects.order_by("pk"))


def outcome_lines(logs: JsonLogs) -> list[dict[str, object]]:
    """The log lines that say what a run did."""
    return [e for e in logs.entries() if str(e["message"]).startswith(OUTCOMES)]


def log_in(platform: PlatformContext, email: str, password: str) -> int:
    """Log in on the Platform and return the status code."""
    response = platform.new_client().post(
        PLATFORM_LOGIN_URL, {"email": email, "password": password}, format="json"
    )
    return response.status_code


@pytest.fixture
def platform(platform_site: PlatformContext) -> PlatformContext:
    """The Platform subdomain, with no Platform Admin yet."""
    return platform_site


class TestNoValues:
    def test_does_nothing_and_says_so_once(
        self, platform: PlatformContext, monkeypatch: pytest.MonkeyPatch, json_logs: JsonLogs
    ) -> None:
        set_values(monkeypatch, None, None, None)

        run()

        assert admins() == []
        lines = outcome_lines(json_logs)
        assert [(e["level"], e["message"]) for e in lines] == [("INFO", NOT_SET)]

    def test_empty_values_count_as_not_set(
        self, platform: PlatformContext, monkeypatch: pytest.MonkeyPatch, json_logs: JsonLogs
    ) -> None:
        set_values(monkeypatch, "", "  ", "")

        run()

        assert admins() == []
        assert json_logs.only(NOT_SET)["level"] == "INFO"


class TestCreates:
    def test_a_new_email_creates_a_platform_admin_who_can_log_in(
        self, platform: PlatformContext, monkeypatch: pytest.MonkeyPatch, json_logs: JsonLogs
    ) -> None:
        set_values(monkeypatch)

        run()

        [admin] = admins()
        assert admin.name == GOOD_NAME
        assert admin.email == "ops@platform.test"
        lines = outcome_lines(json_logs)
        assert [(e["level"], e["message"]) for e in lines] == [("INFO", f"{CREATED}{admin.pk}")]
        assert log_in(platform, "ops@platform.test", GOOD_PASSWORD) == status.HTTP_200_OK

    def test_the_name_is_trimmed(
        self, platform: PlatformContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_values(monkeypatch, name="  Platform Ops  ")

        run()

        assert admins()[0].name == GOOD_NAME

    def test_a_second_different_email_creates_a_second_and_leaves_the_first(
        self, platform: PlatformContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_values(monkeypatch)
        run()
        set_values(
            monkeypatch, name="Rescue", email="rescue@platform.test", password=OTHER_PASSWORD
        )

        run()

        first, second = admins()
        assert (first.name, first.email) == (GOOD_NAME, "ops@platform.test")
        assert (second.name, second.email) == ("Rescue", "rescue@platform.test")
        assert log_in(platform, "ops@platform.test", GOOD_PASSWORD) == status.HTTP_200_OK
        assert log_in(platform, "rescue@platform.test", OTHER_PASSWORD) == status.HTTP_200_OK


class TestNeverChangesAnExistingOne:
    @pytest.mark.parametrize(
        "email", ["ops@platform.test", "OPS@PLATFORM.TEST", " Ops@platform.Test "]
    )
    def test_the_same_email_again_changes_nothing(
        self,
        platform: PlatformContext,
        monkeypatch: pytest.MonkeyPatch,
        json_logs: JsonLogs,
        email: str,
    ) -> None:
        set_values(monkeypatch)
        run()
        [before] = admins()
        json_logs.clear()
        set_values(monkeypatch, name="Someone Else", email=email, password=OTHER_PASSWORD)

        run()

        [after] = admins()
        assert after.pk == before.pk
        assert after.name == GOOD_NAME
        assert after.auth_version == before.auth_version
        assert log_in(platform, "ops@platform.test", GOOD_PASSWORD) == status.HTTP_200_OK
        assert log_in(platform, "ops@platform.test", OTHER_PASSWORD) == status.HTTP_400_BAD_REQUEST
        lines = outcome_lines(json_logs)
        assert [(e["level"], e["message"]) for e in lines] == [("INFO", f"{EXISTS}{before.pk}")]

    def test_a_run_that_loses_a_race_for_the_email_creates_nothing(
        self, platform: PlatformContext, monkeypatch: pytest.MonkeyPatch, json_logs: JsonLogs
    ) -> None:
        # Another deploy creates the same email between this run's lookup and its insert.
        set_values(monkeypatch)
        winner = platform.add_admin(email="ops@platform.test")
        monkeypatch.setattr(
            "platform_admin.management.commands.ensure_platform_admin.find_platform_admin",
            lambda _email: None,
        )

        run()

        assert [admin.pk for admin in admins()] == [winner.pk]
        assert json_logs.only(f"{EXISTS}{winner.pk}")["level"] == "INFO"


class TestEmailsAreUniqueIgnoringCase:
    def test_the_database_refuses_a_second_admin_with_the_same_email_in_another_case(
        self, platform: PlatformContext
    ) -> None:
        platform.add_admin(email="ops@platform.test")

        with schema_context(PUBLIC_SCHEMA), pytest.raises(IntegrityError), transaction.atomic():
            PlatformAdmin.objects.create(name="Copy", email="OPS@platform.test")


BAD_VALUES = [
    pytest.param({"name": None}, [NAME_VAR], id="missing-name"),
    pytest.param({"email": None}, [EMAIL_VAR], id="missing-email"),
    pytest.param({"password": None}, [PASSWORD_VAR], id="missing-password"),
    pytest.param({"name": None, "password": None}, [NAME_VAR, PASSWORD_VAR], id="two-missing"),
    pytest.param({"email": "not-an-email"}, [EMAIL_VAR], id="bad-email"),
    pytest.param({"email": f"{'a' * 250}@platform.test"}, [EMAIL_VAR], id="long-email"),
    pytest.param({"password": "weakpass"}, [PASSWORD_VAR], id="weak-password"),
    pytest.param({"password": "Aa1!" + "x" * 200}, [PASSWORD_VAR], id="long-password"),
    pytest.param({"name": "N" * 201}, [NAME_VAR], id="long-name"),
    pytest.param({"name": "<script>alert(1)</script>"}, [NAME_VAR], id="html-name"),
    pytest.param({"name": "Robert'); DROP TABLE platform_admin;--"}, [NAME_VAR], id="sql-name"),
    pytest.param({"name": "Ops\nAdmin"}, [NAME_VAR], id="line-break-name"),
    pytest.param(
        {"name": "<b>x</b>", "email": "nope", "password": "short"},
        [NAME_VAR, EMAIL_VAR, PASSWORD_VAR],
        id="all-bad",
    ),
]


class TestIncompleteOrInvalidValues:
    @pytest.mark.parametrize(("values", "named"), BAD_VALUES)
    def test_creates_nothing_and_names_each_bad_variable_without_its_value(
        self,
        platform: PlatformContext,
        monkeypatch: pytest.MonkeyPatch,
        json_logs: JsonLogs,
        values: dict[str, str | None],
        named: list[str],
    ) -> None:
        given = {"name": GOOD_NAME, "email": GOOD_EMAIL, "password": GOOD_PASSWORD, **values}
        set_values(monkeypatch, **given)

        printed = run()

        assert admins() == []
        [line] = outcome_lines(json_logs)
        assert line["level"] == "ERROR"
        assert line["message"] == f"{INVALID}{', '.join(named)}"
        for value in given.values():
            if value and value.strip():
                assert value not in json_logs.text()
                assert value not in printed


class TestNoSecretsInOutput:
    def test_password_and_email_never_reach_the_logs_or_the_output(
        self, platform: PlatformContext, monkeypatch: pytest.MonkeyPatch, json_logs: JsonLogs
    ) -> None:
        set_values(monkeypatch)
        printed = run()
        printed += run()

        for secret in (GOOD_PASSWORD, "ops@platform.test", GOOD_EMAIL):
            assert secret not in json_logs.text()
            assert secret not in printed

    def test_each_run_has_one_correlation_id(
        self, platform: PlatformContext, monkeypatch: pytest.MonkeyPatch, json_logs: JsonLogs
    ) -> None:
        set_values(monkeypatch)

        run()

        ids = {entry["correlation_id"] for entry in json_logs.entries()}
        assert len(ids) == 1
        assert ids != {""}


class TestTheOldCommandIsGone:
    def test_create_platform_admin_no_longer_exists(self, platform: PlatformContext) -> None:
        assert "create_platform_admin" not in get_commands()
        assert COMMAND in get_commands()
