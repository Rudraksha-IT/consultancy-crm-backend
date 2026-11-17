"""Hide a switched-off Module's endpoints: they answer 404, as if they didn't exist."""

from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from modules.services import is_module_on
from modules.types import Module
from tenancy.context import get_request_consultancy


def requires_module(module: Module) -> type[BasePermission]:
    """Return a DRF permission that answers 404 unless `module` is on for the Consultancy.

    Put it after the authentication check, so a logged-out caller can't learn what is on.
    """

    class ModuleIsOn(BasePermission):
        def has_permission(self, request: Request, view: APIView) -> bool:
            if not is_module_on(get_request_consultancy(request), module):
                raise NotFound
            return True

    ModuleIsOn.__name__ = ModuleIsOn.__qualname__ = f"{module.name.title()}ModuleIsOn"
    return ModuleIsOn
