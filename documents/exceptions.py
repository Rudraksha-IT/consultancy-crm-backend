from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from documents.models import DocumentType


class DocumentStorageError(Exception):
    """The file couldn't be stored (storage unreachable or refused it)."""


class DuplicateDocumentTypeError(Exception):
    """Another Document Type already has this name (in any case, spaces tidied).

    `existing` is that type, when it was found before saving.
    """

    def __init__(self, existing: DocumentType | None = None) -> None:
        super().__init__()
        self.existing = existing


class TooManyDocumentTypesError(Exception):
    """The Consultancy already has the most Document Types allowed."""


class TooManyDocumentsError(Exception):
    """The Student already has the most Documents allowed."""


class TooManyFilesError(Exception):
    """The Document would hold more files than allowed."""


class RejectedNeedsNewCopyError(Exception):
    """A Rejected Document is replaced by a new copy; files are never added to it."""


class AlreadyCheckedError(Exception):
    """The Document has already been marked correct or rejected."""


class NotNewestCopyError(Exception):
    """A newer copy of this Document Type has been uploaded since; only the newest is checked."""


class EveryoneNeedsTypeError(Exception):
    """A Document Type every Student needs can't be asked for again, or removed."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class AlreadyUploadedError(Exception):
    """A file is already uploaded for this Document Type, so it stays on the Student's checklist."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class DocumentTypeInUseError(Exception):
    """The Document Type is asked for somewhere or has files, so it can't be removed."""


class NotAskedError(Exception):
    """The Document Type isn't asked for this Student alone."""


class VisaResultNeededError(Exception):
    """A letter for a Visa at Applied must say Granted or Refused (issue 77)."""


class VisaAnsweredError(Exception):
    """The letter says another result than the Visa already has, which is final."""

    def __init__(self, current: str) -> None:
        super().__init__(current)
        self.current = current


class AlreadyInBinError(Exception):
    """The Document or file (or its Document) is already in the Bin (issue 79)."""


class NotInBinError(Exception):
    """The Document or file isn't in the Bin."""


class NotWhoPutItInError(Exception):
    """Only whoever put it in the Bin may take it out."""


class OnlyFileError(Exception):
    """A Document's only file in use can't be deleted alone; the Document is deleted instead."""


class DocumentInBinError(Exception):
    """The Document is in the Bin, so it can't be checked or added to."""


class KeepsExamplesError(Exception):
    """The Document Type has Document Examples (even in the Bin), so it is never removed."""
