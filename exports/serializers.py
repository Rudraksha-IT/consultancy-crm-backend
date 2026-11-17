"""What the export endpoints accept. The server never trusts the browser (issues README)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from config.serializers import StrictSerializer
from exports.lists import LIST_EXPORTS
from exports.models import ExportList
from students.serializers import CHOOSE_FROM_LIST_MESSAGE, StrictChoiceField

FILTERS_MESSAGE = "These filters can't be used. Reload the list and try again."
# The list's page is never part of an export: it is always the whole filtered list.
PAGE_FIELD = "page"


class ExportSerializer(StrictSerializer[None]):
    """One list to export, and that list's filters as its screen sent them."""

    list = StrictChoiceField(
        choices=ExportList.choices, error_messages={"invalid_choice": CHOOSE_FROM_LIST_MESSAGE}
    )
    filters = serializers.JSONField()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Check the filters with the list's own query serializer, as its screen's GET does.

        A refusal never says which filter or repeats what was sent: the screen only sends what
        the list accepted, so anything else came from somewhere else.
        """
        filters = attrs["filters"]
        if not _only_text(filters):
            raise serializers.ValidationError({"filters": [FILTERS_MESSAGE]})
        query = LIST_EXPORTS[ExportList(attrs["list"])].query(data=filters)
        if not query.is_valid():
            raise serializers.ValidationError({"filters": [FILTERS_MESSAGE]})
        attrs["query"] = query
        return attrs

    def export_list(self) -> ExportList:
        """The list asked for."""
        return ExportList(self.validated_data["list"])

    def query(self) -> StrictSerializer[None]:
        """The list's query serializer, checked."""
        query: StrictSerializer[None] = self.validated_data["query"]
        return query

    def sent_filters(self) -> dict[str, Any]:
        """The filters as sent, once checked."""
        filters: dict[str, Any] = self.validated_data["filters"]
        return filters


def _only_text(filters: object) -> bool:
    """An object of text values (or lists of text), without the page."""
    if not isinstance(filters, Mapping) or PAGE_FIELD in filters:
        return False
    return all(
        isinstance(value, str)
        or (isinstance(value, list) and all(isinstance(item, str) for item in value))
        for value in filters.values()
    )


class ExportRecordListQuerySerializer(StrictSerializer[None]):
    """The page of Export Records asked for."""

    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def page_number(self) -> int:
        """The page asked for; the first if none."""
        number: int = self.validated_data.get("page", 1)
        return number
