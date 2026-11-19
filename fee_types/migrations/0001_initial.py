import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    operations = [
        migrations.CreateModel(
            name="FeeType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=60)),
                ("position", models.PositiveIntegerField()),
                ("hidden", models.BooleanField(default=False)),
                ("is_other", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("position", "pk"),
                "constraints": [
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower("name"),
                        name="fee_types_unique_name",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("is_other", True)),
                        fields=("is_other",),
                        name="fee_types_one_other",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("is_other", False), ("hidden", False), _connector="OR"),
                        name="fee_types_other_never_hidden",
                    ),
                ],
            },
        ),
    ]
