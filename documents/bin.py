"""The Bin for Documents (spec 118, 119; issue 79).

Anyone but an Admin who deletes a Document, or one of its files, puts it in the Bin with a
reason. It stays on the Student's list, but nobody can open, check or use it, and the checklist
counts it as missing. Whoever put it there may take it out; an Admin restores it or deletes it
for good, which removes the stored files too. An Admin's own deletes skip the Bin. Nothing empties
the Bin by itself.

Every step is written to the Student's History, and the bell tells the Admins of each new item
and tells whoever put it there what an Admin did. Call inside the Consultancy's schema; who may do
these is the views' job. Logs carry ids only, never names or reasons.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from course_files.models import CourseFile
from documents.exceptions import (
    AlreadyInBinError,
    NotInBinError,
    NotWhoPutItInError,
    OnlyFileError,
)
from documents.models import Document, DocumentFile, InTheBin
from documents.storage import document_storage
from history.models import HistoryKind
from history.services import Change, record
from notifications.services import notify_bin_added, notify_bin_answered
from notifications.types import BinNotice
from staff.actors import actor_name, stored_staff
from staff.models import Staff
from students.models import Student

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BinTarget:
    """What is meant: a whole Document, or one of its files (`file_id`)."""

    document_id: int
    file_id: int | None = None


@dataclass(frozen=True)
class BinItem:
    """One item in the Bin: a whole Document, or one file of a Document still in use."""

    document: Document
    file: DocumentFile | None

    @property
    def held(self) -> InTheBin:
        """The record that says who put it in the Bin, when and why."""
        return self.file if self.file is not None else self.document

    @property
    def binned_at(self) -> datetime:
        """When it was put in the Bin."""
        at = self.held.binned_at
        assert at is not None  # noqa: S101  # only items in the Bin are made
        return at


def item_name(document: Document, file: DocumentFile | None) -> str:
    """How History and the bell name it: "Passport", or "page-2.pdf of Passport"."""
    type_name = document.document_type.name
    return type_name if file is None else f"{file.file_name} of {type_name}"


def put_in_bin(student: Student, target: BinTarget, reason: str, by: Staff) -> None:
    """Put a Document, or one of its files, in the Bin with the reason.

    Raises:
        Document.DoesNotExist: The Student has no such Document.
        DocumentFile.DoesNotExist: The Document has no such file.
        AlreadyInBinError: It (or its Document) is in the Bin already.
        OnlyFileError: It is the Document's only file in use; delete the Document instead.
    """
    with transaction.atomic():
        document, file = _locked(student, target)
        if document.is_in_bin or (file is not None and file.is_in_bin):
            raise AlreadyInBinError
        if file is not None and _files_in_use(document) <= 1:
            raise OnlyFileError
        held: InTheBin = file if file is not None else document
        held.binned_at = timezone.now()
        held.binned_by = stored_staff(by)
        held.binned_by_name = actor_name(by)
        held.bin_reason = reason
        held.save(update_fields=["binned_at", "binned_by", "binned_by_name", "bin_reason"])
        name = item_name(document, file)
        record(student, HistoryKind.DOCUMENT_BINNED, by, Change(after=name, detail=reason))
        notify_bin_added(BinNotice(student=student, item=name, reason=reason, by=by))
    _log("Put in the Bin", student, target, by)


def take_out(student: Student, target: BinTarget, by: Staff) -> None:
    """Take something back out of the Bin; only whoever put it there may.

    Raises:
        Document.DoesNotExist: The Student has no such Document.
        DocumentFile.DoesNotExist: The Document has no such file.
        NotInBinError: It isn't in the Bin.
        NotWhoPutItInError: Someone else put it there.
    """
    with transaction.atomic():
        document, file = _locked(student, target)
        held = _in_bin(document, file)
        if held.binned_by_id is None or held.binned_by_id != _stored_id(by):
            raise NotWhoPutItInError
        _empty(held)
        record(
            student,
            HistoryKind.DOCUMENT_TAKEN_OUT,
            by,
            Change(after=item_name(document, file)),
        )
    _log("Taken out of the Bin", student, target, by)


def _stored_id(by: Staff) -> int | None:
    """The id stored for `by`: their own, or None for a Platform Admin."""
    stored = stored_staff(by)
    return None if stored is None else stored.pk


def restore(student: Student, target: BinTarget, by: Staff) -> None:
    """Restore something from the Bin (an Admin) and tell whoever put it there.

    Raises:
        Document.DoesNotExist: The Student has no such Document.
        DocumentFile.DoesNotExist: The Document has no such file.
        NotInBinError: It isn't in the Bin.
    """
    with transaction.atomic():
        document, file = _locked(student, target)
        held = _in_bin(document, file)
        name, reason, put_by = item_name(document, file), held.bin_reason, held.binned_by_id
        _empty(held)
        record(student, HistoryKind.DOCUMENT_RESTORED, by, Change(after=name, detail=reason))
        notify_bin_answered(
            BinNotice(student=student, item=name, reason=reason, by=by),
            recipient_id=put_by,
            restored=True,
        )
    _log("Restored from the Bin", student, target, by)


def delete_for_good(student: Student, target: BinTarget, by: Staff) -> None:
    """Delete a Document, or one of its files, with its stored files (an Admin).

    From the Bin, whoever put it there is told. Otherwise it is the Admin's own delete, which
    skips the Bin; a Document's only file in use is never deleted alone. The stored files go first:
    if storage fails, nothing is deleted from the records and it can be tried again.

    Raises:
        Document.DoesNotExist: The Student has no such Document.
        DocumentFile.DoesNotExist: The Document has no such file.
        OnlyFileError: It is the Document's only file in use.
        DocumentStorageError: A stored file couldn't be removed; nothing is deleted.
    """
    with transaction.atomic():
        document, file = _locked(student, target)
        held: InTheBin = file if file is not None else document
        from_bin = held.is_in_bin
        if file is not None and not from_bin and _files_in_use(document) <= 1:
            raise OnlyFileError
        name, reason, put_by = item_name(document, file), held.bin_reason, held.binned_by_id
        files = [file] if file is not None else list(document.files.all())
        for one in files:
            document_storage().delete(one.storage_key)
        if file is not None:
            file.delete()
        else:
            document.delete()
        if from_bin:
            change = Change(after=name, detail=reason)
            record(student, HistoryKind.DOCUMENT_DELETED_FROM_BIN, by, change)
            notify_bin_answered(
                BinNotice(student=student, item=name, reason=reason, by=by),
                recipient_id=put_by,
                restored=False,
            )
        else:
            record(student, HistoryKind.DOCUMENT_DELETED, by, Change(after=name))
    _log("Deleted for good" if from_bin else "Document deleted", student, target, by)


def bin_items() -> list[BinItem]:
    """Everything in the Bin, newest first.

    Whole Documents (with all their files), and single files of Documents still in use.
    """
    documents = list(
        Document.objects.filter(binned_at__isnull=False)
        .select_related("student", "document_type")
        .prefetch_related("files")
    )
    files = list(
        DocumentFile.objects.filter(
            binned_at__isnull=False, document__binned_at__isnull=True
        ).select_related("document__student", "document__document_type")
    )
    items = [BinItem(document, None) for document in documents]
    items += [BinItem(file.document, file) for file in files]
    return sorted(items, key=lambda item: (item.binned_at, item.document.pk), reverse=True)


def count_in_bin() -> int:
    """How many items wait in the Bin (Admin Home): Documents, their files and Course Files."""
    whole = Document.objects.filter(binned_at__isnull=False).count()
    single = DocumentFile.objects.filter(
        binned_at__isnull=False, document__binned_at__isnull=True
    ).count()
    course_files = CourseFile.objects.filter(binned_at__isnull=False).count()
    return whole + single + course_files


def file_in_bin(file_id: int) -> DocumentFile:
    """A file waiting in the Bin, alone or with its Document (opened by an Admin, Bin tab).

    Raises:
        DocumentFile.DoesNotExist: There is no such file in the Bin.
    """
    file = DocumentFile.objects.select_related("document").get(pk=file_id)
    if not (file.is_in_bin or file.document.is_in_bin):
        raise DocumentFile.DoesNotExist
    return file


def _locked(student: Student, target: BinTarget) -> tuple[Document, DocumentFile | None]:
    """The Student locked, their Document and the file meant, if any; call in a transaction."""
    locked = Student.objects.select_for_update().get(pk=student.pk)
    document = Document.objects.select_related("document_type").get(
        student=locked, pk=target.document_id
    )
    if target.file_id is None:
        return document, None
    return document, DocumentFile.objects.get(document=document, pk=target.file_id)


def _files_in_use(document: Document) -> int:
    return document.files.filter(binned_at__isnull=True).count()


def _in_bin(document: Document, file: DocumentFile | None) -> InTheBin:
    """The record meant, if it is in the Bin itself.

    Raises:
        NotInBinError: It isn't.
    """
    held: InTheBin = file if file is not None else document
    if not held.is_in_bin:
        raise NotInBinError
    return held


def _empty(held: InTheBin) -> None:
    held.binned_at = None
    held.binned_by = None
    held.binned_by_name = ""
    held.bin_reason = ""
    held.save(update_fields=["binned_at", "binned_by", "binned_by_name", "bin_reason"])


def _log(what: str, student: Student, target: BinTarget, by: Staff) -> None:
    logger.info(
        "%s: document_id=%s, file_id=%s, student_id=%s, by_staff_id=%s",
        what,
        target.document_id,
        target.file_id,
        student.pk,
        by.pk,
    )
