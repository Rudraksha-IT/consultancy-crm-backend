"""Drop the record of who added and removed each Office Network (first live fixes, ticket 02).

The product owner decided on 2026-11-22 that this history isn't wanted, on the page or kept
anywhere, so the table and its rows go from every Consultancy's schema.
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    """Delete the OfficeNetworkChange model and its table."""

    dependencies = [("office_networks", "0002_public_id")]

    operations = [migrations.DeleteModel(name="OfficeNetworkChange")]
