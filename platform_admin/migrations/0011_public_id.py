"""Give every Platform Admin, their changes and Support Visit its own UUID, its id outside the server (ADR 0027)."""

from __future__ import annotations

from django.db import migrations

from config.public_ids import add_public_id


class Migration(migrations.Migration):
    """Add `public_id`, fill each existing row with its own UUID, then make it unique."""

    dependencies = [("platform_admin", "0010_email_test")]

    operations = [
        *add_public_id("platform_admin", "platformadmin"),
        *add_public_id("platform_admin", "platformadminchange"),
        *add_public_id("platform_admin", "supportvisit"),
    ]
