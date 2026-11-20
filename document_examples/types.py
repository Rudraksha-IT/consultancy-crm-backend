from __future__ import annotations

from dataclasses import dataclass
from typing import IO

from documents.files import FileKind


@dataclass(frozen=True)
class NewExample:
    """A checked example file: its bytes, what it really is, the name to show and its size."""

    content: IO[bytes]
    kind: FileKind
    file_name: str
    size: int
