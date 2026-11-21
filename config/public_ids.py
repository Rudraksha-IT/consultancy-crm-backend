"""Record ids outside the server: every record is known by a UUID, never its number (ADR 0027).

Each record keeps its database number as the primary key (joins stay small and fast) and gets a
second, unique `public_id`. Addresses, API answers and requests, file paths and exports use only
`public_id`; the number never leaves the server.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.db import migrations, models
from django.urls import register_converter

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


class RecordConverter:
    """An address part naming a record by UUID, handed to the view as its number.

    Only a UUID in the server's spelling matches (anything else is 404 at once). A UUID that
    names no record of `model` in this office (the schema chosen from the host) reaches the view
    as 0, which no record has: the view checks the login and Role first, then answers 404 as it
    does for any unknown record.
    """

    regex = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    model: type[models.Model]

    def to_python(self, value: str) -> int:
        """The record's number; 0 (no record) when there is none."""
        return pk_for(self.model._default_manager.all(), uuid.UUID(value))

    def to_url(self, value: int) -> str:
        """The record's UUID, for an address built from its number."""
        records = self.model._default_manager.filter(pk=value)
        return str(records.values_list(PUBLIC_ID, flat=True).get())


def register_record_converters(converters: dict[str, type[models.Model]]) -> None:
    """Make `<name:…>` in an address a record of that model named by UUID (ADR 0027)."""
    for name, model in converters.items():
        converter = type(f"{model.__name__}Converter", (RecordConverter,), {"model": model})
        register_converter(converter, name)
