"""Each refresh token names the device it belongs to and when that device logged in (issue 38).

Tokens issued before this have no device; each one becomes its own device, logged in when it
was issued. Their tokens carry no `device` claim, so they are refused and the person logs in again.
"""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import F
from django.db.models.functions import Substr


def give_each_old_token_its_own_device(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Fill the new columns for tokens issued before devices were recorded."""
    issued = apps.get_model("auth_tokens", "IssuedRefreshToken")
    issued.objects.update(device_id=Substr("jti", 1, 32), signed_in_at=F("issued_at"))


class Migration(migrations.Migration):
    dependencies = [("auth_tokens", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="issuedrefreshtoken",
            name="device_id",
            field=models.CharField(default="", max_length=32),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="issuedrefreshtoken",
            name="signed_in_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(give_each_old_token_its_own_device, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="issuedrefreshtoken",
            name="signed_in_at",
            field=models.DateTimeField(),
        ),
    ]
