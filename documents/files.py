"""What an uploaded Document may be: checked from its bytes, never from what the browser says.

Pure functions, no database or storage.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

# The largest file accepted (a scanned passport or a bank statement fits easily).
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
# How many files one upload may carry (the pages of one Document; issue 78).
MAX_FILES_AT_ONCE = 10
# How big the files of one upload may be together. The Next.js server holds a whole upload in
# memory before passing it on, so this keeps it small on the 1 GiB server.
MAX_UPLOAD_BYTES = 2 * MAX_DOCUMENT_BYTES
# How many bytes the longest signature below needs.
SIGNATURE_BYTES = 8
FILE_NAME_MAX_CHARACTERS = 150
FALLBACK_NAME = "document"
# Characters never kept in a file name: control characters, path separators and quotes.
_UNSAFE_NAME_CHARACTERS = re.compile(r'[\x00-\x1f\x7f/\\"<>:|?*]')


@dataclass(frozen=True)
class FileKind:
    """A file type Documents may have, with the type and extension it is always stored with."""

    content_type: str
    extension: str
    # Extensions a person may have typed for this kind (lowercase, with the dot).
    known_extensions: tuple[str, ...]
    signature: bytes


PDF = FileKind("application/pdf", ".pdf", (".pdf",), b"%PDF-")
JPEG = FileKind("image/jpeg", ".jpg", (".jpg", ".jpeg"), b"\xff\xd8\xff")
PNG = FileKind("image/png", ".png", (".png",), b"\x89PNG\r\n\x1a\n")
FILE_KINDS = (PDF, JPEG, PNG)


def detect_kind(start: bytes) -> FileKind | None:
    """The kind of file whose first bytes are `start`, or None when it isn't a PDF or photo."""
    return next((kind for kind in FILE_KINDS if start.startswith(kind.signature)), None)


def safe_file_name(name: str, kind: FileKind) -> str:
    """The name to show and download the file as.

    No folders or unsafe characters, at most 150 characters, and always ending in the extension
    of what the file really is (so "x.html" holding a PDF becomes "x.html.pdf").
    """
    base = PureWindowsPath(PurePosixPath(name).name).name
    cleaned = _UNSAFE_NAME_CHARACTERS.sub("", unicodedata.normalize("NFC", base)).strip(" .")
    stem = cleaned
    if PurePosixPath(cleaned).suffix.lower() in kind.known_extensions:
        stem = cleaned[: -len(PurePosixPath(cleaned).suffix)]
    stem = stem.strip(" .") or FALLBACK_NAME
    return stem[: FILE_NAME_MAX_CHARACTERS - len(kind.extension)] + kind.extension
