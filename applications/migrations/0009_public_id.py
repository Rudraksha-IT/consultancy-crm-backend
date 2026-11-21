"""Give every Application, Visa its own UUID, its id outside the server (ADR 0027)."""

from __future__ import annotations

from django.db import migrations

from config.public_ids import add_public_id


class Migration(migrations.Migration):
    """Add `public_id`, fill each existing row with its own UUID, then make it unique."""

    dependencies = [("applications", "0008_offered_on")]

    operations = [
        *add_public_id("applications", "application"),
        *add_public_id("applications", "visa"),
    ]
