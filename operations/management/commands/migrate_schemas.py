"""django-tenants' `migrate_schemas`, with one correlation ID per run and its start and end logged.

Each Consultancy's migration lines carry its schema (django-tenants switches the connection).
Run over every office (`--tenant` without `--schema`), it skips offices still being set up or
whose setup failed (issue 60): their schema belongs to the setup job, which migrates it itself
with `--schema`.
"""

from __future__ import annotations

import logging
from typing import Any

from django_tenants.management.commands.migrate_schemas import (
    GET_EXECUTOR_FUNCTION,
    MigrateSchemasCommand,
)
from django_tenants.utils import get_public_schema_name, schema_exists

from config.commands import LoggedCommand
from tenancy.models import Consultancy, SetupState

logger = logging.getLogger(__name__)


class Command(LoggedCommand, MigrateSchemasCommand):  # type: ignore[misc]  # untyped base
    """Migrate the public schema and every ready Consultancy's, or one named schema."""

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401  # Django's signature
        """Migrate what the options name, as django-tenants does, leaving out unready offices."""
        # SyncCommon reads the options into sync_public, sync_tenant, schema_name, executor.
        super(MigrateSchemasCommand, self).handle(*args, **options)
        public = get_public_schema_name()
        executor = GET_EXECUTOR_FUNCTION(codename=self.executor)(self.args, self.options)
        if self.sync_public:
            executor.run_migrations(tenants=[public])
        if not self.sync_tenant:
            return
        if self.schema_name and self.schema_name != public:
            if not schema_exists(self.schema_name):
                message = f'Schema "{self.schema_name}" does not exist'
                raise RuntimeError(message)
            executor.run_migrations(tenants=[self.schema_name])
            return
        executor.run_migrations(tenants=_ready_office_schemas(public))


def _ready_office_schemas(public: str) -> list[str]:
    """Every set-up office's schema; offices still being set up or failed are left out."""
    offices = Consultancy.objects.exclude(schema_name=public).order_by("pk")
    skipped = offices.exclude(setup_state=SetupState.READY).count()
    if skipped:
        logger.info("offices not set up yet left out of migrations: count=%s", skipped)
    ready = offices.filter(setup_state=SetupState.READY)
    return [str(schema) for schema in ready.values_list("schema_name", flat=True)]
