"""Record ids outside the server: every record is known by a UUID, never its number (ADR 0027).

Each record keeps its database number as the primary key (joins stay small and fast) and gets a
second, unique `public_id`. Addresses, API answers and requests, file paths and exports use only
`public_id`; the number never leaves the server.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.db import migrations, models

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor
    from django.db.migrations.operations.base import Operation
    from django.db.models import QuerySet

PUBLIC_ID = "public_id"


def public_id_field() -> models.UUIDField[uuid.UUID | str, uuid.UUID]:
    """The column holding a record's UUID: made once, unique, never changed."""
    return models.UUIDField(default=uuid.uuid4, unique=True, editable=False)


def add_public_id(app_label: str, model_name: str) -> list[Operation]:
    """The migration steps that give a model's existing rows each their own UUID.

    Adding the column with a default would give every existing row the same value (Django
    computes it once), so: add it empty, fill each row with its own `gen_random_uuid()`, then
    make it unique and required.
    """

    def fill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
        table = schema_editor.quote_name(apps.get_model(app_label, model_name)._meta.db_table)
        column = schema_editor.quote_name(PUBLIC_ID)
        schema_editor.execute(
            f"UPDATE {table} SET {column} = gen_random_uuid() WHERE {column} IS NULL"  # noqa: S608  # names quoted, no input
        )

    return [
        migrations.AddField(
            model_name=model_name,
            name=PUBLIC_ID,
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(model_name=model_name, name=PUBLIC_ID, field=public_id_field()),
    ]


def pk_for[Record: models.Model](records: QuerySet[Record], public_id: uuid.UUID) -> int:
    """The database number of the record in `records` with this UUID; 0 (no record) if none.

    Services work with database numbers inside the server; views turn the UUID they were given
    into one here, looking only among `records` (for example one Student's Applications).
    """
    found = records.filter(public_id=public_id).values_list("pk", flat=True).first()
    return 0 if found is None else int(found)
