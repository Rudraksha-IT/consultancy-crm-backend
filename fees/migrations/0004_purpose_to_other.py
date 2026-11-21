"""Fees typed before Fee Types become "Other", with the old text kept as the note (spec 76)."""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import F


def move_purpose_to_other(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    rows = apps.get_model("fees", "Fee").objects.filter(fee_type__isnull=True)
    if not rows.exists():
        return
    other = apps.get_model("fee_types", "FeeType").objects.get(is_other=True)
    rows.update(fee_type=other, note=F("purpose"))


class Migration(migrations.Migration):
    dependencies = [("fees", "0003_fee_type_and_note")]

    operations = [migrations.RunPython(move_purpose_to_other, migrations.RunPython.noop)]
