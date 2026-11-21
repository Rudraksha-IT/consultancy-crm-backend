"""Payments recorded together share one receipt (ticket 07): add the receipt, empty at first."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0005_fee_type_required")]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="receipt",
            field=models.PositiveBigIntegerField(null=True),
        ),
    ]
