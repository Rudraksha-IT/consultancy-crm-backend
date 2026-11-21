"""Every Payment gets a Fee Type and a note (spec 71, 76); the old purpose stays until 0005."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0002_platform_admin_actor"),
        ("fee_types", "0002_default_fee_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="fee_type",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="fee_types.feetype",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="note",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
