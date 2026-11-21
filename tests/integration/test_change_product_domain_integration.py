"""`change_product_domain` moves every office's address to a new product domain.

Run when the product moves to its own domain: only the domain part of each office's host
changes (`acme.old.example` becomes `acme.new.example`); the subdomain, the schema and every
record stay as they are. A dry run by default; `--apply` changes everything in one step.
"""

from __future__ import annotations

import re
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django_tenants.utils import schema_context
from pytest_django.fixtures import Settings
from rest_framework import status

from staff.models import Role, Staff
from tenancy.context import activate_public_schema
from tenancy.models import Domain
from tenancy.product_domain import Move, ProductDomainError, apply_moves, plan_moves
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, api_client

# The domains the product moves to and back from in these tests: inputs to the command, not
# environment values.
NEW_DOMAIN = "new-crm.example"
OTHER_DOMAIN = "elsewhere.example"
CURRENT_CONSULTANCY_URL = "/api/consultancy/"


def run(*args: str) -> str:
    """Run the command and return what it printed."""
    out = StringIO()
    call_command("change_product_domain", *args, stdout=out)
    activate_public_schema()
    return out.getvalue()


def hosts() -> list[tuple[str, bool, bool]]:
    """Every office host with whether it is primary and switched on, sorted by host."""
    with schema_context("public"):
        rows = Domain.objects.values_list("domain", "is_primary", "is_active")
        return sorted((str(host), bool(primary), bool(active)) for host, primary, active in rows)


def use_domain(settings: Settings, domain: str) -> None:
    """Point the running settings at `domain`, as the env files do after the switch."""
    settings.PRODUCT_DOMAIN = domain
    settings.ALLOWED_HOSTS = [f".{domain}", "testserver"]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The first office."""
    return make_consultancy("ABC Education", "abcedu")


@pytest.fixture
def xyz(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """A second office, switched off (as a Terminated office's hosts are)."""
    office = make_consultancy("XYZ Overseas", "xyzoverseas")
    with schema_context("public"):
        Domain.objects.filter(tenant=office.consultancy).update(is_active=False)
    return office


class TestDryRun:
    """Without `--apply` nothing changes; the planned moves are printed."""

    def test_prints_the_moves_and_changes_nothing(
        self, settings: Settings, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        before = hosts()
        output = run("--from", settings.PRODUCT_DOMAIN, "--to", NEW_DOMAIN)
        assert hosts() == before
        assert f"abcedu.{settings.PRODUCT_DOMAIN} -> abcedu.{NEW_DOMAIN}" in output
        assert f"xyzoverseas.{settings.PRODUCT_DOMAIN} -> xyzoverseas.{NEW_DOMAIN}" in output
        assert "Dry run" in output
        assert "--apply" in output


class TestApply:
    """`--apply` moves every office host under the old domain, and only those."""

    def test_moves_every_office_keeping_primary_and_switched_off(
        self, settings: Settings, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        output = run("--from", settings.PRODUCT_DOMAIN, "--to", NEW_DOMAIN, "--apply")
        assert hosts() == [
            (f"abcedu.{NEW_DOMAIN}", True, True),
            (f"xyzoverseas.{NEW_DOMAIN}", True, False),
        ]
        assert "Moved 2 office addresses" in output
        assert f"PRODUCT_DOMAIN={NEW_DOMAIN}" in output

    def test_leaves_hosts_under_another_domain_alone(
        self, settings: Settings, abc: ConsultancyContext
    ) -> None:
        with schema_context("public"):
            Domain.objects.create(
                domain=f"abcedu.{OTHER_DOMAIN}", tenant=abc.consultancy, is_primary=False
            )
        run("--from", settings.PRODUCT_DOMAIN, "--to", NEW_DOMAIN, "--apply")
        assert hosts() == [
            (f"abcedu.{OTHER_DOMAIN}", False, True),
            (f"abcedu.{NEW_DOMAIN}", True, True),
        ]

    def test_a_host_that_only_ends_in_the_same_letters_is_left_alone(
        self, settings: Settings, abc: ConsultancyContext
    ) -> None:
        # "abcedu.my<domain>" is not under ".<domain>": only whole labels count.
        lookalike = f"abcedu.my{settings.PRODUCT_DOMAIN}"
        with schema_context("public"):
            Domain.objects.create(domain=lookalike, tenant=abc.consultancy, is_primary=False)
        run("--from", settings.PRODUCT_DOMAIN, "--to", NEW_DOMAIN, "--apply")
        assert (lookalike, False, True) in hosts()

    def test_reads_domains_case_insensitively_and_with_a_trailing_dot(
        self, settings: Settings, abc: ConsultancyContext
    ) -> None:
        run("--from", f"{settings.PRODUCT_DOMAIN.upper()}.", "--to", NEW_DOMAIN.upper(), "--apply")
        assert hosts() == [(f"abcedu.{NEW_DOMAIN}", True, True)]


class TestBothWays:
    """The office answers on the new domain after the move, and on the old one after moving back."""

    def test_moves_there_and_back(self, settings: Settings, abc: ConsultancyContext) -> None:
        old_domain = settings.PRODUCT_DOMAIN
        before = hosts()

        run("--from", old_domain, "--to", NEW_DOMAIN, "--apply")
        use_domain(settings, NEW_DOMAIN)
        moved = api_client(f"abcedu.{NEW_DOMAIN}").get(CURRENT_CONSULTANCY_URL)
        assert moved.status_code == status.HTTP_200_OK
        assert moved.json()["name"] == "ABC Education"

        run("--from", NEW_DOMAIN, "--to", old_domain, "--apply")
        use_domain(settings, old_domain)
        back = api_client(f"abcedu.{old_domain}").get(CURRENT_CONSULTANCY_URL)
        assert back.status_code == status.HTTP_200_OK
        assert back.json()["name"] == "ABC Education"
        assert hosts() == before

    def test_the_old_address_no_longer_finds_the_office(
        self, settings: Settings, abc: ConsultancyContext
    ) -> None:
        old_host = abc.host
        run("--from", settings.PRODUCT_DOMAIN, "--to", NEW_DOMAIN, "--apply")
        response = api_client(old_host).get(CURRENT_CONSULTANCY_URL)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_schema_and_records_stay(self, settings: Settings, abc: ConsultancyContext) -> None:
        admin = abc.add_staff(Role.ADMIN)
        run("--from", settings.PRODUCT_DOMAIN, "--to", NEW_DOMAIN, "--apply")
        abc.consultancy.refresh_from_db()
        assert abc.consultancy.schema_name == "abcedu"
        with schema_context("abcedu"):
            assert list(Staff.objects.values_list("email", flat=True)) == [admin.email]


class TestRefused:
    """Bad input is refused with a clear message, and nothing is changed."""

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "bad domain.example",
            "-start.example",
            "end-.example",
            "two..dots.example",
            "under_score.example",
            "<script>.example",
            "x.example;DROP TABLE tenancy_domain",
            "https://new.example",
            "new.example/path",
            "a" * 64 + ".example",
            ("a" * 60 + ".") * 5 + "example",
        ],
    )
    def test_refuses_a_bad_new_domain(
        self, settings: Settings, abc: ConsultancyContext, bad: str
    ) -> None:
        before = hosts()
        with pytest.raises(CommandError, match="--to is not a domain name"):
            run("--from", settings.PRODUCT_DOMAIN, f"--to={bad}", "--apply")
        assert hosts() == before

    def test_refuses_a_bad_old_domain(self, abc: ConsultancyContext) -> None:
        with pytest.raises(CommandError, match="--from is not a domain name"):
            run("--from", "not a domain", "--to", NEW_DOMAIN, "--apply")

    def test_refuses_the_same_domain(self, settings: Settings, abc: ConsultancyContext) -> None:
        with pytest.raises(CommandError, match="already"):
            run("--from", settings.PRODUCT_DOMAIN, "--to", settings.PRODUCT_DOMAIN, "--apply")

    def test_refuses_when_no_office_is_under_the_old_domain(self, abc: ConsultancyContext) -> None:
        before = hosts()
        with pytest.raises(
            CommandError, match=re.escape("No office address ends in .typo.example")
        ):
            run("--from", "typo.example", "--to", NEW_DOMAIN, "--apply")
        assert hosts() == before

    def test_refuses_everything_when_one_new_address_is_taken(
        self, settings: Settings, abc: ConsultancyContext, xyz: ConsultancyContext
    ) -> None:
        with schema_context("public"):
            Domain.objects.create(
                domain=f"xyzoverseas.{NEW_DOMAIN}", tenant=abc.consultancy, is_primary=False
            )
        before = hosts()
        with pytest.raises(
            CommandError, match=re.escape(f"xyzoverseas.{NEW_DOMAIN} is already taken")
        ):
            run("--from", settings.PRODUCT_DOMAIN, "--to", NEW_DOMAIN, "--apply")
        assert hosts() == before

    def test_refuses_a_new_address_that_would_be_too_long(
        self, settings: Settings, abc: ConsultancyContext
    ) -> None:
        # Each label fits, but "abcedu." in front of it passes 253 characters.
        long_domain = ".".join(["a" * 61] * 4) + ".ex"
        with pytest.raises(CommandError, match="too long"):
            run("--from", settings.PRODUCT_DOMAIN, "--to", long_domain, "--apply")


class TestLogging:
    """The log gets counts only."""

    def test_logs_the_count(
        self, settings: Settings, abc: ConsultancyContext, json_logs: JsonLogs
    ) -> None:
        run("--from", settings.PRODUCT_DOMAIN, "--to", NEW_DOMAIN, "--apply")
        assert json_logs.with_message("product domain changed: offices=1")


class TestNestedDomains:
    """One domain inside the other (local `*.localhost` trials) moves there and back."""

    def test_moves_into_a_subdomain_and_back(
        self, settings: Settings, abc: ConsultancyContext
    ) -> None:
        old_domain = settings.PRODUCT_DOMAIN
        inner = f"inner.{old_domain}"
        before = hosts()
        run("--from", old_domain, "--to", inner, "--apply")
        assert hosts() == [(f"abcedu.{inner}", True, True)]
        with pytest.raises(CommandError, match="nothing to move"):
            run("--from", old_domain, "--to", inner, "--apply")
        run("--from", inner, "--to", old_domain, "--apply")
        assert hosts() == before


class TestChangedWhileMoving:
    """A host that vanished between planning and moving stops the whole move."""

    def test_nothing_moves_when_a_planned_host_is_gone(
        self, settings: Settings, abc: ConsultancyContext
    ) -> None:
        moves = plan_moves(settings.PRODUCT_DOMAIN, NEW_DOMAIN)
        gone = Move(f"gone.{settings.PRODUCT_DOMAIN}", f"gone.{NEW_DOMAIN}")
        before = hosts()
        with pytest.raises(ProductDomainError, match="changed while moving"):
            apply_moves([*moves, gone])
        assert hosts() == before
