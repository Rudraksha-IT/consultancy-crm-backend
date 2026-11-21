"""Give every Student, AcademicRecord, TestScore, WorkExperience its own UUID, its id outside the server (ADR 0027)."""

from __future__ import annotations

from django.db import migrations

from config.public_ids import add_public_id


class Migration(migrations.Migration):
    """Add `public_id`, fill each existing row with its own UUID, then make it unique."""

    dependencies = [("students", "0007_details_version")]

    operations = [
        *add_public_id("students", "student"),
        *add_public_id("students", "academicrecord"),
        *add_public_id("students", "testscore"),
        *add_public_id("students", "workexperience"),
    ]
