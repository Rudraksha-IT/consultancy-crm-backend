"""The deploy's migrate step: the Platform Admin is made before any office's schema (issue 59)."""

from __future__ import annotations

import io
from typing import Any

import pytest
from django.core import management
from django_tenants.utils import schema_context

from platform_admin.models import PlatformAdmin
from tests.conftest import PUBLIC_SCHEMA, JsonLogs, PlatformContext

COMMAND = "migrate_on_deploy"
STEP_MODULE = "operations.management.commands.migrate_on_deploy"


def platform_admin_count() -> int:
    """How many Platform Admins exist now."""
    with schema_context(PUBLIC_SCHEMA):
        return PlatformAdmin.objects.count()


class Recorder:
    """Stands in for `call_command`: records each step and what existed when it started.

    `ensure_platform_admin` really runs; the migrations and the directory rebuild are recorded
    only (the test database is already migrated).
    """

    def __init__(self, failing: str | None = None) -> None:
        self.steps: list[tuple[str, dict[str, Any], int]] = []
        self.failing = failing

    def __call__(self, name: str, **options: Any) -> None:  # noqa: ANN401  # call_command's
        """Record the step, then run it if it is the Platform Admin step."""
        self.steps.append((name, options, platform_admin_count()))
        if name == self.failing:
            raise management.CommandError(name)
        if name == "ensure_platform_admin":
            management.call_command(name, stdout=io.StringIO())


@pytest.fixture
def deploy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PLATFORM_ADMIN_* values a deploy would have."""
    monkeypatch.setenv("PLATFORM_ADMIN_NAME", "Platform Ops")
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "ops@platform.test")
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "Deploy-Secret-2026!")


class TestMigrateOnDeploy:
    def test_runs_public_schema_then_platform_admin_then_offices_then_the_directory(
        self,
        platform_site: PlatformContext,
        deploy_values: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = Recorder()
        monkeypatch.setattr(f"{STEP_MODULE}.call_command", recorder)

        management.call_command(COMMAND)

        assert [(name, options) for name, options, _ in recorder.steps] == [
            ("migrate_schemas", {"shared": True, "interactive": False}),
            ("ensure_platform_admin", {}),
            ("migrate_schemas", {"tenant": True, "interactive": False}),
            ("rebuild_staff_directory", {}),
        ]

    def test_with_an_empty_database_the_admin_exists_before_the_offices_are_migrated(
        self,
        platform_site: PlatformContext,
        deploy_values: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = Recorder()
        monkeypatch.setattr(f"{STEP_MODULE}.call_command", recorder)

        management.call_command(COMMAND)

        admins_when_started = {
            (name, options.get("tenant", False)): count for name, options, count in recorder.steps
        }
        assert admins_when_started[("migrate_schemas", False)] == 0
        assert admins_when_started[("migrate_schemas", True)] == 1

    def test_bad_platform_admin_values_never_stop_the_deploy(
        self,
        platform_site: PlatformContext,
        monkeypatch: pytest.MonkeyPatch,
        json_logs: JsonLogs,
    ) -> None:
        monkeypatch.setenv("PLATFORM_ADMIN_NAME", "<script>")
        monkeypatch.delenv("PLATFORM_ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("PLATFORM_ADMIN_PASSWORD", raising=False)
        recorder = Recorder()
        monkeypatch.setattr(f"{STEP_MODULE}.call_command", recorder)

        management.call_command(COMMAND)

        assert [name for name, _, _ in recorder.steps][-1] == "rebuild_staff_directory"
        assert platform_admin_count() == 0
        assert "<script>" not in json_logs.text()

    def test_a_failed_public_migration_stops_before_anything_else(
        self,
        platform_site: PlatformContext,
        deploy_values: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = Recorder(failing="migrate_schemas")
        monkeypatch.setattr(f"{STEP_MODULE}.call_command", recorder)

        with pytest.raises(management.CommandError):
            management.call_command(COMMAND)

        assert [name for name, _, _ in recorder.steps] == ["migrate_schemas"]
        assert platform_admin_count() == 0
