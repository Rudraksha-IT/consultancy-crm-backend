from __future__ import annotations

from dataclasses import dataclass
from typing import IO

from applications.models import Application, Visa
from documents.files import FileKind
from documents.groups import DocumentGroup
from documents.models import DocumentType


@dataclass(frozen=True)
class NewFile:
    """One checked file: its bytes, what it really is, the name to show and its size."""

    content: IO[bytes]
    kind: FileKind
    file_name: str
    size: int


@dataclass(frozen=True)
class NewDocument:
    """A checked upload: its type and its files (one or more, in the order sent)."""

    document_type: DocumentType
    files: tuple[NewFile, ...]
    # The Application or Visa of the same Student it is for, if any (at most one).
    application: Application | None = None
    visa: Visa | None = None


@dataclass(frozen=True)
class NewDocumentType:
    """A Document Type to add, whether every Student needs it, and its usual group."""

    name: str
    needed_by_everyone: bool
    usual_group: DocumentGroup = DocumentGroup.APPLYING
