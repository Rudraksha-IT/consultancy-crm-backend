"""Employee details (mobile, job title, joining date) and the invited/active/deactivated status.

`is_active` becomes a column generated from `status`, so the two can never disagree.
"""

import django.utils.timezone
from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def status_from_is_active(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Staff marked inactive before this change have left."""
    staff = apps.get_model("staff", "Staff")
    staff.objects.filter(is_active=False).update(status="deactivated")


def is_active_from_status(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Undo: only active Staff stay active."""
    staff = apps.get_model("staff", "Staff")
    staff.objects.exclude(status="active").update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0002_staff_auth_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="mobile",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="staff",
            name="job_title",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="staff",
            name="joined_on",
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
        migrations.AddField(
            model_name="staff",
            name="status",
            field=models.CharField(
                choices=[
                    ("invited", "Invited, not joined yet"),
                    ("active", "Active"),
                    ("deactivated", "Has left"),
                ],
                default="active",
                max_length=20,
            ),
        ),
        migrations.RunPython(status_from_is_active, is_active_from_status),
        migrations.RemoveField(model_name="staff", name="is_active"),
        migrations.AddField(
            model_name="staff",
            name="is_active",
            field=models.GeneratedField(
                db_persist=True,
                expression=models.ExpressionWrapper(
                    models.Q(("status", "active")), output_field=models.BooleanField()
                ),
                output_field=models.BooleanField(),
            ),
        ),
    ]
