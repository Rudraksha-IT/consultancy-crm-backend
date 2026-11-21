"""Give every Batch, BatchMembership, Attendance its own UUID, its id outside the server (ADR 0027)."""

from __future__ import annotations

from django.db import migrations

from config.public_ids import add_public_id


class Migration(migrations.Migration):
    """Add `public_id`, fill each existing row with its own UUID, then make it unique."""

    dependencies = [("batches", "0003_platform_admin_actor")]

    operations = [
        *add_public_id("batches", "batch"),
        *add_public_id("batches", "batchmembership"),
        *add_public_id("batches", "attendance"),
    ]
