"""Each refresh token names its device, "Chrome on Windows" (issue 71).

One new column with a fixed default; tokens issued before it read "Unknown browser on Unknown
system". No data is copied, so it is quick on every schema.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("auth_tokens", "0003_support_access")]

    operations = [
        migrations.AddField(
            model_name="issuedrefreshtoken",
            name="device_name",
            field=models.CharField(default="Unknown browser on Unknown system", max_length=60),
        ),
    ]
