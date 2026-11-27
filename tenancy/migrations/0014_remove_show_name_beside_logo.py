"""The office name always shows beside the logo; the choice to hide it is gone (owner, 2026-11-27)."""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    """Drop the Consultancy's stored show-name choice."""

    dependencies = [("tenancy", "0013_remove_consultancy_theme")]

    operations = [migrations.RemoveField(model_name="consultancy", name="show_name_beside_logo")]
