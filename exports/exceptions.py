from __future__ import annotations


class ExportRecordIsAppendOnlyError(Exception):
    """Something tried to change or delete an Export Record. Records are only ever added."""

    def __init__(self) -> None:
        super().__init__("Export Records can't be changed or deleted.")


class TooManyRowsError(Exception):
    """The list asked for has more rows than one export may hold."""
