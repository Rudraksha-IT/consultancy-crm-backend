"""Give every WorkSession, WorkSessionChange, WorkingDayChange, Holiday, WorkFromHome its own UUID, its id outside the server (ADR 0027)."""

from __future__ import annotations

from django.db import migrations

from config.public_ids import add_public_id


class Migration(migrations.Migration):
    """Add `public_id`, fill each existing row with its own UUID, then make it unique."""

    dependencies = [("clock_in", "0004_weekly_days_off")]

    operations = [
        *add_public_id("clock_in", "worksession"),
        *add_public_id("clock_in", "worksessionchange"),
        *add_public_id("clock_in", "workingdaychange"),
        *add_public_id("clock_in", "holiday"),
        *add_public_id("clock_in", "workfromhome"),
    ]
