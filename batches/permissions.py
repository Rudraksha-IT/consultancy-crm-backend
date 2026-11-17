"""DRF permission checks for Batches that ask the one policy in `access.policy`."""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from access.policy import BATCH_ROLES_MESSAGE, BatchAction, why_cannot_on_batches
from modules.permissions import requires_module
from modules.types import Module
from staff.models import Staff

BatchesModuleIsOn = requires_module(Module.BATCHES)


def batch_permission(action: BatchAction | None = None) -> type[BasePermission]:
    """Return a permission for `action`; with none, reading needs SEE and changing CHANGE."""

    class CanUseBatches(BasePermission):
        message = BATCH_ROLES_MESSAGE

        def has_permission(self, request: Request, view: APIView) -> bool:
            user = request.user
            if not isinstance(user, Staff):
                return False
            wanted = action or (
                BatchAction.SEE if request.method in SAFE_METHODS else BatchAction.CHANGE
            )
            reason = why_cannot_on_batches(user, wanted)
            if reason is not None:
                self.message = reason
            return reason is None

    return CanUseBatches
