"""Local demo data on the first start (asked 2026-11-17).

`seed_demo` runs once the stack is up (the compose `seed` service). It loads the Acme demo only
when SEED_DEMO_DATA is "true" (the local sample) and the Acme office isn't there yet, so a restart
or a deploy never loads it again or touches real data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command

from platform_admin.management.commands import seed_demo
from tests.conftest import ConsultancyFactory, JsonLogs

SEED_VAR = "SEED_DEMO_DATA"
OFF = "demo data off; skipped"
THERE = "demo data already there; skipped"
LOADED = "demo data loaded"


@pytest.fixture
def scripts_run(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the demo script instead of running it (it builds a whole office)."""
    ran: list[str] = []
    monkeypatch.setattr(seed_demo, "run_script", ran.append)
    return ran


@pytest.mark.django_db
@pytest.mark.parametrize("value", [None, "", "false", "no", "1", "TRUE-ish"])
def test_does_nothing_unless_turned_on(
    value: str | None,
    monkeypatch: pytest.MonkeyPatch,
    scripts_run: list[str],
    json_logs: JsonLogs,
) -> None:
    """Off by default: anything but "true" skips, so production never loads demo data."""
    if value is None:
        monkeypatch.delenv(SEED_VAR, raising=False)
    else:
        monkeypatch.setenv(SEED_VAR, value)

    call_command("seed_demo")

    assert scripts_run == []
    assert OFF in json_logs.text()


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["true", "True", " TRUE "])
def test_loads_the_demo_on_a_fresh_stack(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    scripts_run: list[str],
    json_logs: JsonLogs,
) -> None:
    """Turned on and no Acme office yet: the demo script runs once."""
    monkeypatch.setenv(SEED_VAR, value)

    call_command("seed_demo")

    assert len(scripts_run) == 1
    assert Path(scripts_run[0]).is_file()
    assert Path(scripts_run[0]).name == "acme_seed.py"
    assert LOADED in json_logs.text()


@pytest.mark.django_db
def test_skips_when_the_acme_office_is_already_there(
    make_consultancy: ConsultancyFactory,
    monkeypatch: pytest.MonkeyPatch,
    scripts_run: list[str],
    json_logs: JsonLogs,
) -> None:
    """A second start (or an office called acme already made) never loads it again."""
    make_consultancy("Acme Education Consultancy", "acme")
    monkeypatch.setenv(SEED_VAR, "true")

    call_command("seed_demo")

    assert scripts_run == []
    assert THERE in json_logs.text()


@pytest.mark.django_db
def test_another_office_does_not_stop_the_demo(
    make_consultancy: ConsultancyFactory,
    monkeypatch: pytest.MonkeyPatch,
    scripts_run: list[str],
) -> None:
    """Only the Acme office counts as "already there"."""
    make_consultancy("ABC Education", "abcedu")
    monkeypatch.setenv(SEED_VAR, "true")

    call_command("seed_demo")

    assert len(scripts_run) == 1
