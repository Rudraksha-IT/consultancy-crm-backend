from __future__ import annotations


class ClockInRecordIsAppendOnlyError(Exception):
    """Something tried to change or delete a Work Session Change or Working Day Change."""

    def __init__(self) -> None:
        super().__init__("Clock-in records can't be changed or deleted.")


class ClockRefusedError(Exception):
    """Clocking in or out isn't allowed; the message says why in plain words."""
