"""Every Fee now needs a Fee Type; the free-text purpose is gone (spec 76)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("fees", "0004_purpose_to_other")]

    operations = [
        migrations.AlterField(
            model_name="fee",
            name="fee_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="fee_types.feetype",
            ),
        ),
        migrations.RemoveField(model_name="fee", name="purpose"),
    ]
