from __future__ import annotations

from django.apps import AppConfig


class OperationsConfig(AppConfig):
    """Commands run by the deployment, wrapped to log like requests (issue 58). No models.

    Listed first in INSTALLED_APPS, so its `migrate_schemas` replaces django-tenants' own.
    """

    name = "operations"
