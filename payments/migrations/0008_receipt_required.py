"""Every Payment now has a receipt, found by its number (ticket 07)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0007_receipt_is_own_id")]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="receipt",
            field=models.PositiveBigIntegerField(db_index=True),
        ),
    ]
