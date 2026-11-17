"""Every new Consultancy starts with the Document Types most Students bring (issue 16)."""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

NEEDED_BY_EVERY_STUDENT = (
    "Passport",
    "Citizenship",
    "SEE Marksheet",
    "+2 Transcript",
    "IELTS/PTE Score Card",
    "Bank Statement",
    "Sponsor Letter",
    "Statement of Purpose",
)


def add_default_types(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    document_type = apps.get_model("documents", "DocumentType")
    for name in NEEDED_BY_EVERY_STUDENT:
        document_type.objects.get_or_create(name=name, defaults={"needed_by_everyone": True})


class Migration(migrations.Migration):
    dependencies = [("documents", "0001_initial")]

    operations = [migrations.RunPython(add_default_types, migrations.RunPython.noop)]
