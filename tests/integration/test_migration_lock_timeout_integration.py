"""A migration that would wait on a busy table fails at once instead of blocking (ticket 14).

During a deploy the old version keeps running while the migrations run. A migration that needs a
lock held by the running app gives up after `MIGRATION_LOCK_TIMEOUT_MS`, before the switch, and the
old version keeps running untouched.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from django.core import management
from django.db import OperationalError, connection
from pytest_django.fixtures import Settings

from operations.migration_lock import migration_lock_timeout

STEP_MODULE = "operations.management.commands.migrate_on_deploy"
BUSY_TABLE = "public.django_migrations"


def lock_timeout() -> str:
    """The lock timeout of Django's connection now."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW lock_timeout")
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture
def busy_table() -> Iterator[None]:
    """Another connection (the running app) holds a lock on a table until the test ends."""
    params = connection.get_connection_params()
    other = psycopg.connect(
        dbname=params["dbname"],
        user=params["user"],
        password=params.get("password"),
        host=params.get("host"),
        port=params.get("port"),
    )
    with other.cursor() as cursor:
        cursor.execute(f"LOCK TABLE {BUSY_TABLE} IN ACCESS EXCLUSIVE MODE")
    yield
    other.rollback()
    other.close()


@pytest.mark.django_db
class TestMigrationLockTimeout:
    def test_is_set_while_migrating_and_put_back_afterwards(self, settings: Settings) -> None:
        settings.MIGRATION_LOCK_TIMEOUT_MS = 5000
        before = lock_timeout()

        with migration_lock_timeout():
            during = lock_timeout()

        assert during == "5s"
        assert lock_timeout() == before

    def test_a_migration_waiting_on_a_busy_table_fails_instead_of_blocking(
        self, settings: Settings, busy_table: None
    ) -> None:
        settings.MIGRATION_LOCK_TIMEOUT_MS = 200

        with (
            pytest.raises(OperationalError, match="lock timeout"),
            migration_lock_timeout(),
            connection.cursor() as cursor,
        ):
            cursor.execute(f"LOCK TABLE {BUSY_TABLE} IN ACCESS EXCLUSIVE MODE")

    def test_every_deploy_step_runs_with_it(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.MIGRATION_LOCK_TIMEOUT_MS = 5000
        seen: list[str] = []

        def record(name: str, **options: Any) -> None:  # noqa: ANN401  # call_command's
            seen.append(lock_timeout())

        monkeypatch.setattr(f"{STEP_MODULE}.call_command", record)

        management.call_command("migrate_on_deploy")

        assert seen == ["5s", "5s", "5s", "5s"]

    def test_the_setting_defaults_to_five_seconds(self) -> None:
        from django.conf import settings as django_settings  # noqa: PLC0415  # the real value

        assert django_settings.MIGRATION_LOCK_TIMEOUT_MS == 5000
