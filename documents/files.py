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
    # The first bytes any file of this kind starts with (one of them).
    signatures: tuple[bytes, ...]
    # False for a kind that is only ever downloaded, never shown in the browser.
    opens_in_browser: bool = True


PDF = FileKind("application/pdf", ".pdf", (".pdf",), (b"%PDF-",))
JPEG = FileKind("image/jpeg", ".jpg", (".jpg", ".jpeg"), (b"\xff\xd8\xff",))
PNG = FileKind("image/png", ".png", (".png",), (b"\x89PNG\r\n\x1a\n",))
# A zip starts with its first entry, or (when it holds nothing) with its end record. It is kept
# as sent: never unzipped, read inside or shown (first live fixes, ticket 09).
ZIP = FileKind(
    "application/zip", ".zip", (".zip",), (b"PK\x03\x04", b"PK\x05\x06"), opens_in_browser=False
)
# What files may be anywhere a Document-like file is uploaded: PDFs and photos.
FILE_KINDS = (PDF, JPEG, PNG)
# A Student's Documents also take zips (ticket 09); nothing else does.
STUDENT_DOCUMENT_KINDS = (*FILE_KINDS, ZIP)
# Content types that are only ever downloaded.
DOWNLOAD_ONLY_TYPES = frozenset(
    kind.content_type for kind in STUDENT_DOCUMENT_KINDS if not kind.opens_in_browser
)


def detect_kind(start: bytes, kinds: tuple[FileKind, ...] = FILE_KINDS) -> FileKind | None:
    """The kind among `kinds` whose first bytes are `start`, or None when it is none of them."""
    return next((kind for kind in kinds if start.startswith(kind.signatures)), None)


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
