"""Document Examples: an instruction and example files on each Document Type (spec 62-66).

Call inside the Consultancy's schema; who may do these is the views' job. Files go to private
storage under the Consultancy's own prefix, in their own folder, never beside a Student's
Documents, and never count on a checklist. Replacing or removing one puts the old file in the
Bin; an Admin restores it or deletes it for good, which removes the stored file too. Logs carry
ids only, never file names or the instruction.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Prefetch, QuerySet
from django.utils import timezone

from document_examples.exceptions import TooManyExamplesError
from document_examples.models import DocumentExample
from document_examples.types import NewExample
from documents.models import DocumentType
from documents.storage import document_storage
from staff.actors import actor_name, stored_staff
from staff.models import Staff
from tenancy.context import get_active_schema_name

# The most example files one Document Type shows at once (those in the Bin don't count).
MAX_EXAMPLES_PER_TYPE = 3
# Why an example went to the Bin (the Bin shows it).
REPLACED_REASON = "Replaced"
REMOVED_REASON = "Removed from the examples"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TypeGuide:
    """What Staff see beside a Document Type on a checklist: its instruction and examples."""

    instruction: str
    examples: tuple[DocumentExample, ...]


def live_examples_prefetch() -> Prefetch[str]:
    """Prefetch a Document Type's examples in use, oldest first, as `live_examples`."""
    return Prefetch(
        "examples",
        queryset=DocumentExample.objects.filter(binned_at__isnull=True),
        to_attr="live_examples",
    )


def guides_for(type_ids: Iterable[int]) -> Mapping[int, TypeGuide]:
    """The instruction and examples in use of each Document Type named, in two queries."""
    types = DocumentType.objects.filter(pk__in=set(type_ids)).prefetch_related(
        live_examples_prefetch()
    )
    return {t.pk: TypeGuide(t.instruction, tuple(getattr(t, "live_examples", ()))) for t in types}


def set_instruction(type_id: int, instruction: str, by: Staff) -> DocumentType:
    """Set (or clear, with "") how a Document Type should look, for Students.

    Raises:
        DocumentType.DoesNotExist: There is no such type here.
    """
    document_type = DocumentType.objects.get(pk=type_id)
    document_type.instruction = instruction
    document_type.save(update_fields=["instruction"])
    logger.info(
        "Document Example instruction set: document_type_id=%s, length=%s, by_staff_id=%s",
        type_id,
        len(instruction),
        by.pk,
    )
    return document_type


def add_example(document_type: DocumentType, new: NewExample, by: Staff) -> DocumentExample:
    """Store the file privately and keep it as an example of the type.

    Raises:
        TooManyExamplesError: The type already has MAX_EXAMPLES_PER_TYPE examples in use.
        DocumentStorageError: The file couldn't be stored; nothing is kept.
    """
    with transaction.atomic():
        locked = _locked_type(document_type.pk)
        if _in_use(locked) >= MAX_EXAMPLES_PER_TYPE:
            raise TooManyExamplesError
        example = _store(locked, new, by)
    logger.info(
        "Document Example added: document_example_id=%s, document_type_id=%s, size=%s, "
        "by_staff_id=%s",
        example.pk,
        locked.pk,
        example.size,
        by.pk,
    )
    return example


def replace_example(old: DocumentExample, new: NewExample, by: Staff) -> DocumentExample:
    """Put `old` in the Bin and keep the new file in its place, all or nothing.

    Raises:
        DocumentStorageError: The new file couldn't be stored; nothing changes.
    """
    with transaction.atomic():
        locked = _locked_type(old.document_type_id)
        _bin(old, REPLACED_REASON, by)
        example = _store(locked, new, by)
    logger.info(
        "Document Example replaced: document_example_id=%s, replaced_id=%s, by_staff_id=%s",
        example.pk,
        old.pk,
        by.pk,
    )
    return example


def put_example_in_bin(example: DocumentExample, by: Staff) -> None:
    """Remove an example file to the Bin; nobody can open or send it while it waits there."""
    _bin(example, REMOVED_REASON, by)
    logger.info(
        "Document Example put in the Bin: document_example_id=%s, by_staff_id=%s",
        example.pk,
        by.pk,
    )


def restore_example(example: DocumentExample, by: Staff) -> None:
    """Take an example file out of the Bin, back on its Document Type.

    Raises:
        TooManyExamplesError: The type already has MAX_EXAMPLES_PER_TYPE examples in use.
    """
    with transaction.atomic():
        locked = _locked_type(example.document_type_id)
        if _in_use(locked) >= MAX_EXAMPLES_PER_TYPE:
            raise TooManyExamplesError
        example.binned_at = None
        example.binned_by = None
        example.binned_by_name = ""
        example.bin_reason = ""
        example.save(update_fields=["binned_at", "binned_by", "binned_by_name", "bin_reason"])
    logger.info(
        "Document Example restored: document_example_id=%s, by_staff_id=%s", example.pk, by.pk
    )


def delete_example_for_good(example: DocumentExample, by: Staff) -> None:
    """Delete an example file in the Bin with its stored file.

    The stored file goes first: if storage fails, the record stays and it can be tried again.

    Raises:
        DocumentStorageError: The stored file couldn't be removed; nothing is deleted.
    """
    example_id = example.pk
    document_storage().delete(example.storage_key)
    example.delete()
    logger.info(
        "Document Example deleted for good: document_example_id=%s, by_staff_id=%s",
        example_id,
        by.pk,
    )


def example_link(example: DocumentExample, *, as_attachment: bool, by: Staff) -> str:
    """A short-lived link to open or save the example file, under its own name."""
    link = document_storage().signed_link(
        example.storage_key,
        file_name=example.file_name,
        content_type=example.content_type,
        as_attachment=as_attachment,
    )
    logger.info(
        "Document Example link issued: document_example_id=%s, by_staff_id=%s",
        example.pk,
        by.pk,
    )
    return link


def live_example(example_id: int) -> DocumentExample:
    """An example file in use (not in the Bin).

    Raises:
        DocumentExample.DoesNotExist: There is no such example, or it is in the Bin.
    """
    return DocumentExample.objects.get(pk=example_id, binned_at__isnull=True)


def binned_example(example_id: int) -> DocumentExample:
    """An example file waiting in the Bin.

    Raises:
        DocumentExample.DoesNotExist: There is no such example in the Bin.
    """
    return DocumentExample.objects.get(pk=example_id, binned_at__isnull=False)


def binned_examples() -> QuerySet[DocumentExample]:
    """Every example file in the Bin, with its Document Type."""
    return DocumentExample.objects.filter(binned_at__isnull=False).select_related("document_type")


def _locked_type(type_id: int) -> DocumentType:
    """The Document Type, locked so two uploads can't both pass the limit."""
    return DocumentType.objects.select_for_update().get(pk=type_id)


def _in_use(document_type: DocumentType) -> int:
    return DocumentExample.objects.filter(
        document_type=document_type, binned_at__isnull=True
    ).count()


def _store(document_type: DocumentType, new: NewExample, by: Staff) -> DocumentExample:
    """Keep the record, then the file; call inside a transaction so a failed store keeps none."""
    folder = f"{get_active_schema_name()}/document-examples/{document_type.public_id}/"
    key = f"{folder}{uuid.uuid4().hex}{new.kind.extension}"
    example = DocumentExample.objects.create(
        document_type=document_type,
        file_name=new.file_name,
        content_type=new.kind.content_type,
        size=new.size,
        storage_key=key,
        uploaded_by=stored_staff(by),
        uploaded_by_name=actor_name(by),
    )
    document_storage().save(key, new.content, new.kind.content_type)
    return example


def _bin(example: DocumentExample, reason: str, by: Staff) -> None:
    example.binned_at = timezone.now()
    example.binned_by = stored_staff(by)
    example.binned_by_name = actor_name(by)
    example.bin_reason = reason
    example.save(update_fields=["binned_at", "binned_by", "binned_by_name", "bin_reason"])
