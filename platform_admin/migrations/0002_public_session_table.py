"""Formerly created the public schema's session table for Platform Admin sessions.

Logins are JWTs now (ADR 0003) and the sessions app is gone, so this does nothing; it stays so
databases that applied it keep a consistent history. 0003 drops the table.
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("platform_admin", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
