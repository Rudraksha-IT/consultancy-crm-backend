from __future__ import annotations

from dataclasses import dataclass
from typing import IO


@dataclass(frozen=True)
class NewCourseFile:
    """A checked PDF to keep on a Course: its bytes, the name to show, its own name and size."""

    content: IO[bytes]
    name: str
    file_name: str
    size: int
