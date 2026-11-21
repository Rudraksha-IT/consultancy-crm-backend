"""The migrations that give existing records their UUIDs (ADR 0027, spec 114).

Every existing row gets its own UUID, unique, and nothing else about it changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.apps import apps as now
from django.apps.registry import Apps
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from students.models import AcademicRecord, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory

BEFORE = [("students", "0007_details_version")]


def _staff(apps: Apps) -> None:
    for n in range(3):
        apps.get_model("staff", "Staff").objects.create(
            name=f"Staff {n}", email=f"staff{n}@office.com", role="counsellor"
        )


def _catalogue(apps: Apps) -> None:
    for n in range(3):
        university = apps.get_model("catalogue", "University").objects.create(
            name=f"University {n}", country="australia"
        )
        apps.get_model("catalogue", "Course").objects.create(
            university=university, name=f"Course {n}", level="bachelor"
        )


def _fee_types(apps: Apps) -> None:
    """Every office starts with its seven Fee Types (a data migration); nothing to add."""


# Ticket 18's kinds: the migration before each app's switch, the rows to make, the models.
OFFICE_KINDS: dict[str, tuple[tuple[str, str], Callable[[Apps], None], list[str]]] = {
    "staff": (("staff", "0008_light_by_default"), _staff, ["Staff"]),
    "catalogue": (("catalogue", "0001_initial"), _catalogue, ["University", "Course"]),
    "fee_types": (("fee_types", "0002_default_fee_types"), _fee_types, ["FeeType"]),
}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The office whose schema is migrated back and forward."""
    return make_consultancy("ABC Education", "abcedu")


def test_each_existing_row_gets_its_own_uuid(abc: ConsultancyContext) -> None:
    """Three Students and their records made before the change each get a different UUID."""
    with tenant_context(abc.consultancy):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        old_apps = executor.loader.project_state(BEFORE).apps
        old_student = old_apps.get_model("students", "Student")
        old_record = old_apps.get_model("students", "AcademicRecord")
        students = [
            old_student.objects.create(name=f"Student {n}", phone=f"+97798000000{n}")
            for n in range(3)
        ]
        for student in students:
            old_record.objects.create(student_id=student.pk, level="plus_two", institution="A")
        with connection.cursor() as cursor:  # check the rows now, so tables can be altered
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

        after = list(Student.objects.order_by("pk").values_list("pk", "name", "public_id"))
        record_ids = list(AcademicRecord.objects.values_list("public_id", flat=True))

    assert [(pk, name) for pk, name, _ in after] == [
        (student.pk, student.name) for student in students
    ]
    student_ids = [public_id for _, _, public_id in after]
    assert all(student_ids)
    assert len(set(student_ids)) == len(students)
    assert all(record_ids)
    assert len(set(record_ids) | set(student_ids)) == len(students) * 2


def test_new_rows_get_a_uuid_by_themselves(abc: ConsultancyContext) -> None:
    """A Student made now has a UUID without anyone setting it."""
    with tenant_context(abc.consultancy):
        first = Student.objects.create(name="Asha", phone="+9779800000001")
        second = Student.objects.create(name="Bina", phone="+9779800000002")

    assert first.public_id
    assert first.public_id != second.public_id


@pytest.mark.parametrize("kind", OFFICE_KINDS)
def test_existing_office_records_each_get_their_own_uuid(
    abc: ConsultancyContext, kind: str
) -> None:
    """Employees, the catalogue and Fee Types made before the change get different UUIDs."""
    before, make_rows, model_names = OFFICE_KINDS[kind]
    app = before[0]
    with tenant_context(abc.consultancy):
        executor = MigrationExecutor(connection)
        executor.migrate([before])
        make_rows(executor.loader.project_state([before]).apps)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        old_rows = {
            name: _rows_without_uuid(executor.loader.project_state([before]).apps, app, name)
            for name in model_names
        }
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        for name in model_names:
            model: Any = now.get_model(app, name)
            rows = list(model.objects.order_by("pk").values())
            assert [{k: v for k, v in row.items() if k != "public_id"} for row in rows] == old_rows[
                name
            ]
            ids = [row["public_id"] for row in rows]
            assert rows
            assert all(ids)
            assert len(set(ids)) == len(ids)


def _rows_without_uuid(apps: Apps, app: str, name: str) -> list[dict[str, object]]:
    model: Any = apps.get_model(app, name)
    return list(model.objects.order_by("pk").values())


# Ticket 19's kinds, on the Platform's own (public) schema: the migration before each switch.
# Stored as an unusable password hash: the rows are never logged in with.
UNUSABLE_PASSWORD = UNUSABLE_PASSWORD_PREFIX
PLATFORM_BEFORE = [
    ("tenancy", "0011_consultancy_office_details"),
    ("platform_admin", "0010_email_test"),
]
PLATFORM_AFTER = [("tenancy", "0012_public_id"), ("platform_admin", "0011_public_id")]


def test_existing_platform_records_each_get_their_own_uuid(abc: ConsultancyContext) -> None:
    """Consultancies and Platform Admins made before the change get different UUIDs."""
    with schema_context(get_public_schema_name()):
        executor = MigrationExecutor(connection)
        executor.migrate(PLATFORM_BEFORE)
        old_apps = executor.loader.project_state(PLATFORM_BEFORE).apps
        for n in range(3):
            old_apps.get_model("platform_admin", "PlatformAdmin").objects.create(
                name=f"Admin {n}", email=f"admin{n}@platform.com", password=UNUSABLE_PASSWORD
            )
            old_apps.get_model("tenancy", "Consultancy").objects.create(
                name=f"Office {n}", schema_name=f"office{n}"
            )
        before = {
            label: _rows_without_uuid(old_apps, *label.split("."))
            for label in ("tenancy.Consultancy", "platform_admin.PlatformAdmin")
        }
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(PLATFORM_AFTER)
        for label, old_rows in before.items():
            model: Any = now.get_model(label)
            rows = list(model.objects.order_by("pk").values())
            assert [{k: v for k, v in row.items() if k != "public_id"} for row in rows] == old_rows
            ids = [row["public_id"] for row in rows]
            assert len(rows) >= len(range(3))
            assert all(ids)
            assert len(set(ids)) == len(ids)
