# Light by default (asked 2026-11-20): the app no longer follows the device's light or dark.

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def device_to_light(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    """Staff who followed their device are light; anyone who chose dark keeps it."""
    staff_model = apps.get_model("staff", "Staff")
    staff_model.objects.filter(colour_mode="device").update(colour_mode="light")


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0007_page_swipe_per_person"),
    ]

    operations = [
        migrations.RunPython(device_to_light, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="staff",
            name="colour_mode",
            field=models.CharField(
                choices=[("light", "Light"), ("dark", "Dark")], default="light", max_length=10
            ),
        ),
    ]
