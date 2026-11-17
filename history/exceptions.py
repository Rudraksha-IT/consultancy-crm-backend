from __future__ import annotations


class HistoryIsAppendOnlyError(Exception):
    """Something tried to change or delete a History entry. Entries are only ever added."""

    def __init__(self) -> None:
        super().__init__("History entries can't be changed or deleted.")
