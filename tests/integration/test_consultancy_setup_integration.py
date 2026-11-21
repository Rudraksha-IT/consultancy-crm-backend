"""Creating a Consultancy in a background job (issue 60).

Create answers 202 at once and builds nothing; the `worker` builds the schema and records the
starting Status, then opens the host. It invites nobody: Admins are added once the office is set
up (issue 64). A failure leaves
nothing behind, and Try again sets it up from the start.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from django_tenants.utils import schema_context, schema_exists, tenant_context
from rest_framework import status
from rest_framework.test import APIClient

from jobs.models import Job, JobState
from platform_admin.models import PlatformAdmin
from platform_admin.setup import CREATE_CONSULTANCY, SubdomainTakenError, start_consultancy_setup
from platform_admin.types import ConsultancyOnboarding
from staff.models import Role, Staff
from tenancy.models import Consultancy, ConsultancyStatusChange, Domain, SetupState
from tenancy.theme import DEFAULT_THEME
from tests.conftest import (
    PUBLIC_SCHEMA,
    TEST_PASSWORD,
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    build_host,
    run_queued_jobs,
    use_access_token,
)
from tests.record_ids import number_of

CONSULTANCIES_URL = "/api/platform/consultancies/"
OFFICE_URL = "/api/consultancy/"
LOGIN_URL = "/api/auth/login/"
JOIN_COMPLETE_URL = "/api/auth/join/complete/"
CENTRAL_LOGIN_URL = "/api/central-login/"
HOST = build_host("everest")
ADMIN_EMAIL = "sita@everest.test"
NEW_PASSWORD = "Kathmandu!2026"  # noqa: S105  # test-only credential
SETUP_FAILED = "The office could not be set up. Try again."
NOT_READY = "This office is still being set up, or its setup failed."
NOT_FAILED = "Only an office whose setup failed can be set up again."
PAID_UNTIL = (timezone.localdate() + timedelta(days=30)).isoformat()
INJECTION = "<script>alert(1)</script>'; DROP TABLE jobs_job; --"


def new_consultancy(**overrides: object) -> dict[str, object]:
    """A valid create-Consultancy body, with some fields replaced."""
    return {
        "name": "Everest Education",
        "subdomain": "everest",
        "contact_email": "info@everest.test",
        **overrides,
    }


def create(client: APIClient, **overrides: object) -> dict[str, Any]:
    """Create a Consultancy through the API, expecting 202, and return its summary."""
    response = client.post(CONSULTANCIES_URL, new_consultancy(**overrides), format="json")
    assert response.status_code == status.HTTP_202_ACCEPTED, response.content
    body: dict[str, Any] = response.json()
    return body


def retry_url(consultancy_id: object) -> str:
    """The Try again endpoint of one Consultancy."""
    return f"{CONSULTANCIES_URL}{consultancy_id}/retry-setup/"


def stored(consultancy_id: int | str) -> Consultancy:
    """The Consultancy as stored now, by its number or the UUID an answer names it by."""
    key = (
        {"pk": consultancy_id} if isinstance(consultancy_id, int) else {"public_id": consultancy_id}
    )
    with schema_context(PUBLIC_SCHEMA):
        found: Consultancy = Consultancy.objects.get(**key)
    return found


def is_host_on(host: str) -> bool:
    """Whether the host is switched on."""
    with schema_context(PUBLIC_SCHEMA):
        return bool(Domain.objects.get(domain=host).is_active)


def jobs() -> list[Job]:
    """Every Job, oldest first."""
    with schema_context(PUBLIC_SCHEMA):
        return list(Job.objects.order_by("pk"))


def emailed_code() -> str:
    """The 6-digit Code in the last email."""
    found = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert found is not None
    return found.group(1)


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformAdmin:
    """The logged-in Platform Admin."""
    return platform_site.log_in()


@pytest.fixture
def failing_migrations(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make the fifth migration applied from now on raise, half-way through a new schema."""
    applied = {"count": 0}
    apply = MigrationExecutor.apply_migration

    def apply_then_fail(self: MigrationExecutor, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        applied["count"] += 1
        if applied["count"] == 5:  # half-way through
            message = "boom half-way, password=hunter2"
            raise RuntimeError(message)
        return apply(self, *args, **kwargs)

    monkeypatch.setattr(MigrationExecutor, "apply_migration", apply_then_fail)
    yield
    monkeypatch.setattr(MigrationExecutor, "apply_migration", apply)


class TestCreateAnswersAtOnce:
    def test_answers_202_setting_up_and_builds_no_schema(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        body = create(platform_site.client)

        assert body["setup_state"] == "setting_up"
        assert body["host"] == HOST
        assert not schema_exists("everest")
        assert not is_host_on(HOST)
        [job] = jobs()
        assert (job.kind, job.state, job.created_by_id) == (
            CREATE_CONSULTANCY,
            JobState.QUEUED,
            ops.pk,
        )

    def test_the_row_shows_setting_up_in_the_list(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        create(platform_site.client)

        [row] = platform_site.client.get(CONSULTANCIES_URL).json()
        assert row["setup_state"] == "setting_up"

    def test_the_job_holds_no_password_token_or_code(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        create(platform_site.client, status="active", paid_until=PAID_UNTIL)

        [job] = jobs()
        assert set(job.payload) == {"consultancy_id", "status", "paid_until"}
        assert not any(word in key for key in job.payload for word in ("password", "token", "code"))


class TestTheJobRunsEndToEnd:
    def test_builds_the_schema_and_opens_the_host_with_no_staff_and_no_email(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        body = create(platform_site.client)

        assert run_queued_jobs() == 1

        assert schema_exists("everest")
        assert is_host_on(HOST)
        assert stored(body["id"]).setup_state == SetupState.READY
        with tenant_context(stored(body["id"])):
            assert not Staff.objects.exists()
        assert mail.outbox == []
        [job] = jobs()
        assert (job.state, job.error) == (JobState.DONE, "")
        assert api_client(HOST).get(OFFICE_URL).status_code == status.HTTP_200_OK

    def test_an_older_job_carrying_an_admin_invites_nobody_and_forgets_them(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        body = create(platform_site.client)
        # Queued before issue 64, when creating also invited an Admin.
        with schema_context(PUBLIC_SCHEMA):
            job = Job.objects.get()
            job.payload = {
                **job.payload,
                "admin_name": "Sita Sharma",
                "admin_email": ADMIN_EMAIL,
                "join_link": f"http://{HOST}/join",
            }
            job.save(update_fields=["payload"])

        run_queued_jobs()

        assert stored(body["id"]).setup_state == SetupState.READY
        with tenant_context(stored(body["id"])):
            assert not Staff.objects.exists()
        assert mail.outbox == []
        [done] = jobs()
        assert ADMIN_EMAIL not in str(done.payload)
        assert "Sita Sharma" not in str(done.payload)

    def test_starting_active_is_recorded_when_the_office_is_ready(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        body = create(platform_site.client, status="active", paid_until=PAID_UNTIL)

        run_queued_jobs()

        office = stored(body["id"])
        assert (office.status, str(office.paid_until)) == ("active", PAID_UNTIL)

    def test_running_the_same_job_twice_changes_nothing_more(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        body = create(platform_site.client)
        run_queued_jobs()
        with schema_context(PUBLIC_SCHEMA):
            Job.objects.update(state=JobState.QUEUED)

        run_queued_jobs()

        assert stored(body["id"]).setup_state == SetupState.READY
        with schema_context(PUBLIC_SCHEMA):
            assert (
                ConsultancyStatusChange.objects.filter(consultancy__public_id=body["id"]).count()
                == 1
            )

    def test_a_schema_left_half_migrated_is_finished_on_the_next_run(
        self, platform_site: PlatformContext, ops: PlatformAdmin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = create(platform_site.client)
        # A worker killed half-way leaves the schema with only some migrations.
        with connection.cursor() as cursor:
            cursor.execute('CREATE SCHEMA "everest"')
        call_command(
            "migrate_schemas",
            tenant=True,
            schema_name="everest",
            interactive=False,
            verbosity=0,
            app_label="staff",
        )

        run_queued_jobs()

        assert stored(body["id"]).setup_state == SetupState.READY
        assert api_client(HOST).get(OFFICE_URL).status_code == status.HTTP_200_OK


class TestTheHostAnswers404UntilReady:
    def test_while_setting_up(self, platform_site: PlatformContext, ops: PlatformAdmin) -> None:
        create(platform_site.client)
        office = api_client(HOST)

        assert office.get(OFFICE_URL).status_code == status.HTTP_404_NOT_FOUND
        assert (
            office.post(LOGIN_URL, {"email": ADMIN_EMAIL, "password": "x"}, format="json")
        ).status_code == status.HTTP_404_NOT_FOUND

    def test_while_failed(
        self, platform_site: PlatformContext, ops: PlatformAdmin, failing_migrations: None
    ) -> None:
        create(platform_site.client)
        run_queued_jobs()

        assert api_client(HOST).get(OFFICE_URL).status_code == status.HTTP_404_NOT_FOUND

    def test_even_if_its_host_were_switched_on(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        create(platform_site.client)
        with schema_context(PUBLIC_SCHEMA):
            Domain.objects.filter(domain=HOST).update(is_active=True)

        assert api_client(HOST).get(OFFICE_URL).status_code == status.HTTP_404_NOT_FOUND


class TestAFailureLeavesNothing:
    def test_a_migration_failing_half_way_leaves_no_schema_and_the_host_off(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        failing_migrations: None,
        json_logs: JsonLogs,
    ) -> None:
        body = create(platform_site.client)

        run_queued_jobs()

        assert not schema_exists("everest")
        assert not is_host_on(HOST)
        [job] = jobs()
        assert (job.state, job.error) == (JobState.FAILED, SETUP_FAILED)
        assert stored(body["id"]).setup_state == SetupState.FAILED
        assert mail.outbox == []
        number = stored(body["id"]).pk
        failed = json_logs.only(
            f"job failed: job_id={job.pk} kind={CREATE_CONSULTANCY} consultancy_id={number}"
        )
        assert failed["level"] == "ERROR"
        assert "RuntimeError" in failed["exception"]
        assert failed["tenant"] == HOST
        assert "hunter2" not in json_logs.text()

    def test_the_row_shows_failed(
        self, platform_site: PlatformContext, ops: PlatformAdmin, failing_migrations: None
    ) -> None:
        create(platform_site.client)
        run_queued_jobs()

        [row] = platform_site.client.get(CONSULTANCIES_URL).json()
        assert row["setup_state"] == "failed"

    def test_try_again_sets_it_up_from_the_start(
        self, platform_site: PlatformContext, ops: PlatformAdmin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        apply = MigrationExecutor.apply_migration

        def fail(self: MigrationExecutor, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            raise RuntimeError

        monkeypatch.setattr(MigrationExecutor, "apply_migration", fail)
        body = create(platform_site.client)
        run_queued_jobs()
        monkeypatch.setattr(MigrationExecutor, "apply_migration", apply)

        response = platform_site.client.post(retry_url(body["id"]))

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.json()["setup_state"] == "setting_up"
        assert run_queued_jobs() == 1
        assert stored(body["id"]).setup_state == SetupState.READY
        assert is_host_on(HOST)
        assert api_client(HOST).get(OFFICE_URL).status_code == status.HTTP_200_OK
        assert [job.state for job in jobs()] == [JobState.FAILED, JobState.DONE]
        assert jobs()[1].created_by_id == ops.pk


class TestRefusedInput:
    def test_a_second_create_for_a_subdomain_still_setting_up_is_refused(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        create(platform_site.client)

        response = platform_site.client.post(
            CONSULTANCIES_URL, new_consultancy(name="Other"), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"subdomain": ["This subdomain is already taken."]}
        assert len(jobs()) == 1

    def test_a_subdomain_whose_setup_failed_stays_taken(
        self, platform_site: PlatformContext, ops: PlatformAdmin, failing_migrations: None
    ) -> None:
        create(platform_site.client)
        run_queued_jobs()

        response = platform_site.client.post(CONSULTANCIES_URL, new_consultancy(), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_two_creates_racing_for_one_subdomain_make_one_office(self, ops: PlatformAdmin) -> None:
        onboarding = ConsultancyOnboarding(
            name="Everest Education",
            subdomain="everest",
            theme=DEFAULT_THEME,
            contact_email="info@everest.test",
        )
        with schema_context(PUBLIC_SCHEMA):
            start_consultancy_setup(onboarding, by=ops)
            with pytest.raises(SubdomainTakenError):
                start_consultancy_setup(onboarding, by=ops)
            assert Consultancy.objects.filter(schema_name="everest").count() == 1

    @pytest.mark.parametrize(
        "extra",
        [
            {"admin_password": TEST_PASSWORD},
            {"admin_name": "Sita Sharma"},
            {"admin_email": ADMIN_EMAIL},
            {"admin_name": "Sita Sharma", "admin_email": ADMIN_EMAIL},
        ],
    )
    def test_an_admin_is_an_unknown_field_when_creating(
        self, platform_site: PlatformContext, ops: PlatformAdmin, extra: dict[str, str]
    ) -> None:
        response = platform_site.client.post(
            CONSULTANCIES_URL, new_consultancy(**extra), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert jobs() == []
        assert mail.outbox == []

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", ["Everest"]),
            ("name", "x" * 201),
            ("name", 42),
            ("subdomain", INJECTION),
            ("subdomain", "Everest"),
            ("subdomain", build_host("everest")),
            ("subdomain", "everest.example.com"),
            ("subdomain", "https://everest"),
            ("subdomain", "ever est"),
            ("subdomain", "x" * 64),
            ("subdomain", 7),
        ],
    )
    def test_bad_values_queue_nothing(
        self, platform_site: PlatformContext, ops: PlatformAdmin, field: str, value: object
    ) -> None:
        response = platform_site.client.post(
            CONSULTANCIES_URL, new_consultancy(**{field: value}), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert field in response.json()
        assert jobs() == []

    def test_only_json_is_taken(self, platform_site: PlatformContext, ops: PlatformAdmin) -> None:
        response = platform_site.client.post(CONSULTANCIES_URL, new_consultancy())

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    def test_try_again_on_a_ready_office_is_refused(
        self,
        make_consultancy: ConsultancyFactory,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = platform_site.client.post(retry_url(abc.consultancy.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": NOT_FAILED}
        assert jobs() == []

    def test_try_again_while_still_setting_up_is_refused(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        body = create(platform_site.client)

        response = platform_site.client.post(retry_url(body["id"]))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert len(jobs()) == 1

    def test_try_again_on_an_unknown_office_is_not_found(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        assert platform_site.client.post(retry_url(uuid.uuid4())).status_code == 404

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_try_again_takes_only_post(
        self, platform_site: PlatformContext, ops: PlatformAdmin, method: str
    ) -> None:
        response = getattr(platform_site.client, method)(retry_url(uuid.uuid4()))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_anonymous_callers_are_refused(self, platform_site: PlatformContext) -> None:
        client = platform_site.new_client()

        assert client.post(CONSULTANCIES_URL, new_consultancy(), format="json").status_code == 401
        assert client.post(retry_url(uuid.uuid4())).status_code == status.HTTP_401_UNAUTHORIZED

    def test_staff_cannot_create_or_try_again(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        client = platform_site.new_client()
        use_access_token(client, abc.tokens_for(abc.add_staff(Role.ADMIN)).access)

        assert client.post(CONSULTANCIES_URL, new_consultancy(), format="json").status_code == 401
        assert client.post(retry_url(abc.consultancy.public_id)).status_code == 401
        assert jobs() == []

    def test_these_endpoints_do_not_exist_on_an_office_host(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        assert abc.client.post(retry_url(abc.consultancy.public_id)).status_code == 404


class TestNothingWorksOnAnOfficeNotReady:
    """Status, Features, Colours and Open as Platform Admin wait until the office is ready."""

    @pytest.mark.parametrize(
        ("path", "method", "body"),
        [
            ("status/", "post", {"status": "terminated", "reason": "Left"}),
            ("modules/", "put", {"batches": True}),
            ("theme/", "put", {"page": "stone", "sidebar": "#ffffff", "accent": "#047857"}),
            ("support-visits/", "post", {"reason": "Help"}),
        ],
    )
    def test_is_refused(
        self,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        path: str,
        method: str,
        body: dict[str, object],
    ) -> None:
        created = create(platform_site.client)

        response = getattr(platform_site.client, method)(
            f"{CONSULTANCIES_URL}{created['id']}/{path}", body, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": NOT_READY}
        assert stored(created["id"]).status == "evaluation"

    def test_deploy_migrations_skip_it(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        create(platform_site.client)

        call_command("migrate_schemas", tenant=True, interactive=False, verbosity=0)

        assert not schema_exists("everest")

    def test_rebuilding_the_staff_directory_skips_it(
        self, platform_site: PlatformContext, ops: PlatformAdmin
    ) -> None:
        create(platform_site.client)

        call_command("rebuild_staff_directory")

    def test_the_central_login_never_emails_its_link(
        self, make_consultancy: ConsultancyFactory, platform_site: PlatformContext
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.add_staff(Role.COUNSELLOR, email="ram@example.com")
        with schema_context(PUBLIC_SCHEMA):
            Consultancy.objects.filter(pk=abc.consultancy.pk).update(
                setup_state=SetupState.SETTING_UP
            )
        mail.outbox.clear()

        response = api_client(build_host("login")).post(
            CENTRAL_LOGIN_URL, {"email": "ram@example.com"}, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert mail.outbox == []


class TestLogs:
    def test_the_job_and_the_request_share_one_correlation_id(
        self, platform_site: PlatformContext, ops: PlatformAdmin, json_logs: JsonLogs
    ) -> None:
        created = create(platform_site.client)
        request_id = json_logs.request_line("POST /api/platform/consultancies/ 202")[
            "correlation_id"
        ]

        run_queued_jobs()

        [job] = jobs()
        assert job.correlation_id == request_id
        done = json_logs.only(
            f"Consultancy set up: consultancy_id={number_of(Consultancy, created['id'])}"
        )
        assert done["correlation_id"] == request_id
        assert done["tenant"] == HOST


class TestIsolation:
    def test_setting_up_one_office_never_touches_another(
        self,
        make_consultancy: ConsultancyFactory,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc_admin = abc.add_staff(Role.ADMIN)

        create(platform_site.client)
        run_queued_jobs()

        with tenant_context(abc.consultancy):
            assert list(Staff.objects.values_list("email", flat=True)) == [abc_admin.email]
        with tenant_context(stored(jobs()[0].payload["consultancy_id"])):
            assert not Staff.objects.filter(email=abc_admin.email).exists()

    def test_a_failed_setup_leaves_another_office_working(
        self,
        make_consultancy: ConsultancyFactory,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
        failing_migrations: None,
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        counsellor = abc.add_staff(Role.COUNSELLOR)

        create(platform_site.client)
        run_queued_jobs()

        assert schema_exists("abcedu")
        login = abc.new_client().post(
            LOGIN_URL, {"email": counsellor.email, "password": TEST_PASSWORD}, format="json"
        )
        assert login.status_code == status.HTTP_200_OK

    def test_try_again_never_reaches_another_office(
        self,
        make_consultancy: ConsultancyFactory,
        platform_site: PlatformContext,
        ops: PlatformAdmin,
    ) -> None:
        abc: ConsultancyContext = make_consultancy("ABC Education", "abcedu")

        response = platform_site.client.post(retry_url(abc.consultancy.public_id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc.consultancy.pk).setup_state == SetupState.READY
