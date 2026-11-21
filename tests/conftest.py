"""Shared fixtures: every API test runs against a real Consultancy schema and subdomain."""

from __future__ import annotations

import io
import json
import logging.config
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.conf import settings as django_settings
from django.db import connection
from django.http import HttpResponseBase
from django_tenants.utils import schema_context, tenant_context
from pytest_django import DjangoDbBlocker
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from auth_tokens.services import issue_tokens
from auth_tokens.types import TokenPair
from config.logging import build_logging
from jobs.services import run_next_job
from platform_admin.models import PlatformAdmin
from platform_admin.services import save_platform_admin
from platform_admin.types import PlatformAdminDetails
from staff.models import Role, Staff
from staff.services import create_staff
from staff.types import NewStaff
from tenancy.context import activate_public_schema
from tenancy.models import Consultancy
from tenancy.services import create_consultancy
from tenancy.subdomains import build_host, get_platform_host
from tests.fakes import FakeDocumentStorage
from tests.record_ids import record_numbers_in_response

__all__ = ["build_host"]

TEST_PASSWORD = "correct-horse-battery-staple"  # noqa: S105  # test-only credential
# Platform Admins are always held to the password rules (issue 59); test-only.
PLATFORM_TEST_PASSWORD = "Correct-Horse-7-Staple!"  # noqa: S105
# What the Next.js server sends as X-Internal-Token (ADR 0002); test-only.
TEST_INTERNAL_API_TOKEN = "test-internal-api-token-0123456789abcdef"  # noqa: S105
# What Django signs login tokens with (ADR 0003); test-only.
TEST_JWT_SIGNING_KEY = "test-jwt-signing-key-0123456789abcdefghij"
# What Django seals SMS and email provider secrets with (issue 25); test-only.
# What the Central login's index of Staff emails is hashed with (issue 44); test-only.
TEST_STAFF_DIRECTORY_KEY = "test-staff-directory-key-0123456789abcdef"
# A Fernet key that seals authenticator secrets (issue 61); test-only.
TEST_TWO_FACTOR_SECRETS_KEY = "dGVzdC10d28tZmFjdG9yLXNlY3JldHMta2V5LTAxMjM="
FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
PUBLIC_SCHEMA = "public"


def api_client(host: str, **headers: str) -> APIClient:
    """Return an API client that calls Django the way the Next.js server does.

    It sends the internal token; `headers` adds more, e.g. `HTTP_X_FORWARDED_HOST`.
    """
    client = APIClient()
    client.defaults.update(
        {"HTTP_HOST": host, "HTTP_X_INTERNAL_TOKEN": TEST_INTERNAL_API_TOKEN, **headers}
    )
    return client


def api_datetime(value: datetime | None) -> str | None:
    """Return a stored moment as the API writes it (ISO 8601, UTC as "Z"), or None."""
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def use_access_token(client: APIClient, access: str) -> None:
    """Send `access` as the Bearer token on every later call, as the Next.js server does."""
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


@dataclass(frozen=True)
class ConsultancyContext:
    """A Consultancy created for a test, plus an API client bound to its host."""

    consultancy: Consultancy
    host: str
    client: APIClient

    def new_client(self) -> APIClient:
        """Return a fresh, logged-out API client bound to this Consultancy's host."""
        return api_client(self.host)

    def add_staff(self, role: Role, email: str | None = None) -> Staff:
        """Create an active Staff member with the given Role and `TEST_PASSWORD`."""
        details = NewStaff(
            name=f"Test {role.label}",
            email=email or f"{role.value}@{self.host}",
            role=role,
        )
        with tenant_context(self.consultancy):
            return create_staff(details, password=TEST_PASSWORD)

    def tokens_for(self, staff: Staff) -> TokenPair:
        """Log a Staff member in on this Consultancy and return their tokens."""
        with tenant_context(self.consultancy):
            return issue_tokens(staff)

    def log_in_as(self, role: Role) -> Staff:
        """Create a Staff member with the given Role and log `client` in as them."""
        staff = self.add_staff(role)
        use_access_token(self.client, self.tokens_for(staff).access)
        return staff


ConsultancyFactory = Callable[[str, str], ConsultancyContext]


@dataclass(frozen=True)
class PlatformContext:
    """The Platform subdomain, plus an API client bound to it."""

    host: str
    client: APIClient

    def new_client(self) -> APIClient:
        """Return a fresh, logged-out API client bound to the Platform host."""
        return api_client(self.host)

    def add_admin(self, email: str = "ops@platform.test") -> PlatformAdmin:
        """Create an active Platform Admin with `PLATFORM_TEST_PASSWORD`."""
        details = PlatformAdminDetails(name="Platform Ops", email=email)
        with schema_context(PUBLIC_SCHEMA):
            return save_platform_admin(details, password=PLATFORM_TEST_PASSWORD)

    def tokens_for(self, admin: PlatformAdmin) -> TokenPair:
        """Log a Platform Admin in on the Platform and return their tokens."""
        with schema_context(PUBLIC_SCHEMA):
            return issue_tokens(admin)

    def log_in(self) -> PlatformAdmin:
        """Create a Platform Admin and log `client` in as them."""
        admin = self.add_admin()
        use_access_token(self.client, self.tokens_for(admin).access)
        return admin


@dataclass(frozen=True)
class JsonLogs:
    """What the standard JSON logging wrote during a test."""

    stream: io.StringIO

    def clear(self) -> None:
        """Forget what was logged so far."""
        self.stream.seek(0)
        self.stream.truncate()

    def text(self) -> str:
        """Return everything logged so far."""
        return self.stream.getvalue()

    def entries(self) -> list[dict[str, Any]]:
        """Return every log line as its JSON object."""
        return [json.loads(line) for line in self.text().splitlines()]

    def with_message(self, start: str) -> list[dict[str, Any]]:
        """Return the entries whose message starts with `start`."""
        return [entry for entry in self.entries() if str(entry["message"]).startswith(start)]

    def only(self, message: str) -> dict[str, Any]:
        """Return the one entry with exactly this message, failing if there isn't exactly one."""
        found = [entry for entry in self.entries() if entry["message"] == message]
        assert len(found) == 1, f"expected one {message!r} line, got {len(found)}"
        return found[0]

    def request_line(self, start: str) -> dict[str, Any]:
        """Return the one request line `"<METHOD> <path> <status> <n>ms"` starting with `start`."""
        line = re.compile(rf"{re.escape(start)} \d+ms")
        found = [entry for entry in self.entries() if line.fullmatch(str(entry["message"]))]
        assert len(found) == 1, f"expected one {start!r} request line, got {len(found)}"
        return found[0]


def capture_json_logs(level: str) -> JsonLogs:
    """Apply the standard JSON logging at `level`, writing into a buffer the test reads.

    Put the settings' logging back afterwards with `restore_logging`.
    """
    logging.config.dictConfig(build_logging(level, "json"))
    stream = io.StringIO()
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(stream)
    return JsonLogs(stream)


def restore_logging() -> None:
    """Put back the logging the settings configure."""
    logging.config.dictConfig(django_settings.LOGGING)


LogsAt = Callable[[str], JsonLogs]

# The worker's default JOB_TIMEOUT_SECONDS (issue 60).
JOB_TIMEOUT = timedelta(seconds=900)


def run_queued_jobs() -> int:
    """Run every queued Job one at a time, as the `worker` does, and return how many ran."""
    ran = 0
    with schema_context(PUBLIC_SCHEMA):
        while run_next_job(JOB_TIMEOUT):
            ran += 1
    activate_public_schema()
    return ran


@pytest.fixture
def json_logs() -> Iterator[JsonLogs]:
    """Apply the standard JSON logging (INFO), writing into a buffer the test reads."""
    yield capture_json_logs("INFO")
    restore_logging()


@pytest.fixture
def json_logs_at() -> Iterator[LogsAt]:
    """Apply the standard JSON logging at a level the test chooses (`LOG_LEVEL`)."""
    yield capture_json_logs
    restore_logging()


_ORIGINAL_REQUEST = APIClient.request


def _guarded_request(self: APIClient, **request: object) -> HttpResponseBase:
    """Send the request, then fail if the JSON answer names a record by number (ADR 0027)."""
    response = _ORIGINAL_REQUEST(self, **request)
    found = record_numbers_in_response(response)
    where = f"{request.get('REQUEST_METHOD', '?')} {request.get('PATH_INFO', '?')}"
    assert not found, f"{where} answered record numbers at {found}"
    return response


@pytest.fixture(autouse=True)
def no_record_numbers_in_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every API answer in the suite names records only by UUID (the guard; issue 19)."""
    monkeypatch.setattr(APIClient, "request", _guarded_request)


@pytest.fixture(autouse=True)
def fast_password_hashing(settings: Settings) -> None:
    """Use a cheap password hasher so tests that create Staff stay fast."""
    settings.PASSWORD_HASHERS = FAST_PASSWORD_HASHERS


@pytest.fixture(autouse=True)
def internal_api_token(settings: Settings) -> None:
    """Give Django the internal token that `api_client` sends."""
    settings.INTERNAL_API_TOKEN = TEST_INTERNAL_API_TOKEN


@pytest.fixture(autouse=True)
def staff_directory_key(settings: Settings) -> None:
    """Give Django the key the Central login's index of Staff emails is hashed with."""
    settings.STAFF_DIRECTORY_KEY = TEST_STAFF_DIRECTORY_KEY


@pytest.fixture(autouse=True)
def jwt_signing_key(settings: Settings) -> None:
    """Give Django the key it signs and checks login tokens with."""
    settings.JWT_SIGNING_KEY = TEST_JWT_SIGNING_KEY


@pytest.fixture(autouse=True)
def fake_document_storage(settings: Settings) -> Iterator[type[FakeDocumentStorage]]:
    """Keep Document files in memory instead of S3, empty for every test."""
    settings.DOCUMENT_STORAGE = "tests.fakes.FakeDocumentStorage"
    FakeDocumentStorage.files.clear()
    FakeDocumentStorage.is_down = False
    yield FakeDocumentStorage
    FakeDocumentStorage.files.clear()
    FakeDocumentStorage.is_down = False


@pytest.fixture(autouse=True)
def two_factor_secrets_key(settings: Settings) -> None:
    """Give Django the key it seals authenticator secrets with (issue 61)."""
    settings.TWO_FACTOR_SECRETS_KEY = TEST_TWO_FACTOR_SECRETS_KEY


# The offices the tests use. Their schemas are built and migrated once per test run.
PREBUILT_SCHEMAS = ("abcedu", "xyzoverseas", "acme", "beta")

# One schema's id counters: (counter, value, is_called).
_READ_SEQUENCES = """
SELECT format('%%I.%%I', schemaname, sequencename), coalesce(last_value, 1), last_value IS NOT NULL
FROM pg_sequences WHERE schemaname = %s
"""
_RESTORE_SEQUENCES = """
SELECT setval(name, value, is_called)
FROM unnest(%s::text[], %s::bigint[], %s::boolean[]) AS counters(name, value, is_called)
"""

Counters = tuple[list[str], list[int], list[bool]]


@dataclass(frozen=True)
class PrebuiltSchemas:
    """The schemas migrated once per run, and their id counters as migrations left them."""

    counters: dict[str, Counters]

    def reset_counters(self, schema: str) -> None:
        """Put the schema's id counters back, so ids continue as in a newly migrated schema."""
        with connection.cursor() as cursor:
            cursor.execute(_RESTORE_SEQUENCES, list(self.counters[schema]))


def _counters_of(schema: str) -> Counters:
    with connection.cursor() as cursor:
        cursor.execute(_READ_SEQUENCES, [schema])
        rows = cursor.fetchall()
    names, values, called = zip(*rows, strict=True)
    return list(names), list(values), list(called)


@pytest.fixture(scope="session")
def prebuilt_schemas(django_db_setup: None, django_db_blocker: DjangoDbBlocker) -> PrebuiltSchemas:
    """Build and migrate the test offices' schemas once, then remove their Consultancy rows.

    Migrating a schema takes about a second; every test used to pay that. The schemas stay
    (as migrations left them); a test's Consultancy row finds its schema already there and
    skips the migrations.
    """
    with django_db_blocker.unblock():
        for subdomain in PREBUILT_SCHEMAS:
            create_consultancy(name=subdomain, schema_name=subdomain, host=build_host(subdomain))
        activate_public_schema()
        Consultancy.objects.filter(schema_name__in=PREBUILT_SCHEMAS).delete()
        return PrebuiltSchemas({schema: _counters_of(schema) for schema in PREBUILT_SCHEMAS})


@pytest.fixture
def make_consultancy(db: None, prebuilt_schemas: PrebuiltSchemas) -> Iterator[ConsultancyFactory]:
    """Create Consultancies (schema + subdomain) and return clients bound to their hosts.

    The surrounding test transaction rolls back everything the test wrote, in every schema.
    A prebuilt schema is reused with its id counters put back, so it behaves like a new one.
    """

    def factory(name: str, subdomain: str) -> ConsultancyContext:
        host = build_host(subdomain)
        consultancy = create_consultancy(name=name, schema_name=subdomain, host=host)
        if subdomain in prebuilt_schemas.counters:
            prebuilt_schemas.reset_counters(subdomain)
        return ConsultancyContext(consultancy, host, api_client(host))

    yield factory
    activate_public_schema()


@pytest.fixture
def platform_site(db: None) -> Iterator[PlatformContext]:
    """Return an API client bound to the Platform host."""
    host = get_platform_host()
    yield PlatformContext(host, api_client(host))
    activate_public_schema()
