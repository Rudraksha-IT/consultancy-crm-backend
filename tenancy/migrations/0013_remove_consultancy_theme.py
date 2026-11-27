"""Office colours are gone: every office shows the one Ivory theme (UI revamp, ticket 04)."""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    """Drop the Consultancy's stored Theme."""

    dependencies = [("tenancy", "0012_public_id")]

    operations = [migrations.RemoveField(model_name="consultancy", name="theme")]
