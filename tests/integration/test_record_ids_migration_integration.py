"""The migrations that give existing records their UUIDs (ADR 0027, spec 114).

Every existing row gets its own UUID, unique, and nothing else about it changes.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import tenant_context

from students.models import AcademicRecord, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory

BEFORE = [("students", "0007_details_version")]


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
