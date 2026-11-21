"""Every Payment saved before ticket 07 was on its own receipt: its number stays its own id."""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import F


def own_receipts(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Give each Payment without a receipt its own id as its receipt number."""
    payment = apps.get_model("payments", "Payment")
    payment.objects.filter(receipt__isnull=True).update(receipt=F("pk"))


class Migration(migrations.Migration):
    dependencies = [("payments", "0006_receipt")]

    operations = [migrations.RunPython(own_receipts, migrations.RunPython.noop)]
