"""Every Consultancy starts with the Fee Types most offices sell (spec 68)."""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

DEFAULT_FEE_TYPES = (
    "Application fee",
    "College / tuition fee",
    "Visa fee",
    "IELTS class",
    "PTE class",
    "Documentation / other services",
    "Other",
)
OTHER = "Other"


def add_default_types(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    fee_type = apps.get_model("fee_types", "FeeType")
    for position, name in enumerate(DEFAULT_FEE_TYPES, start=1):
        fee_type.objects.get_or_create(
            name=name, defaults={"position": position, "is_other": name == OTHER}
        )


class Migration(migrations.Migration):
    dependencies = [("fee_types", "0001_initial")]

    operations = [migrations.RunPython(add_default_types, migrations.RunPython.noop)]
