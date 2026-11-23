"""Saved product logos (first live fixes, ticket 07).

Today's single product logo, if any, becomes the one saved logo, in use, keeping its file and
its version (so its address doesn't change).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import django.db.models.deletion
from django.db import migrations, models

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor


_OLD_KEY = "logo_key"
_OLD_TYPE = "logo_content_type"


def move_the_logo_into_the_list(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Make today's logo the one saved logo, in use."""
    brand_model = apps.get_model("platform_admin", "ProductBrand")
    saved_model = apps.get_model("platform_admin", "SavedProductLogo")
    brand = brand_model.objects.filter(pk=1).first()
    # The old columns, read by name: the model's type here is today's, which no longer has them.
    key: str = getattr(brand, _OLD_KEY, "")
    if brand is None or not key:
        return
    brand.logo = saved_model.objects.create(key=key, content_type=getattr(brand, _OLD_TYPE))
    brand.save(update_fields=["logo"])


class Migration(migrations.Migration):
    dependencies = [
        ("platform_admin", "0012_code_purpose_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="SavedProductLogo",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("key", models.CharField(max_length=200)),
                ("content_type", models.CharField(max_length=40)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddField(
            model_name="productbrand",
            name="logo",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="platform_admin.savedproductlogo",
            ),
        ),
        migrations.RunPython(move_the_logo_into_the_list, migrations.RunPython.noop),
        migrations.RemoveField(model_name="productbrand", name="logo_key"),
        migrations.RemoveField(model_name="productbrand", name="logo_content_type"),
    ]
