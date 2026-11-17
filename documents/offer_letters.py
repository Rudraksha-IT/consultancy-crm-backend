"""Adding a University's Offer Letter to one of a Student's Applications (spec 114; issue 76).

The letter is a Document of the "Offer letter" type, linked to the Application, following the
usual Document file rules. Storing it and making the Application Offer happen in one transaction,
or not at all. Call inside the Consultancy's schema; who may do it is the view's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import IO

from django.db import transaction

from applications.models import Application
from applications.services import record_offer
from applications.statuses import OfferKind
from documents.files import FileKind
from documents.models import Document, DocumentType
from documents.named_types import document_type_named
from documents.services import upload_document
from documents.types import NewDocument, NewFile
from staff.models import Staff
from students.models import Student

# The Document Type every Offer Letter is kept as; made the first time it is needed.
OFFER_LETTER_TYPE_NAME = "Offer letter"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewOfferLetter:
    """A checked Offer Letter: the Student's Application, the kind of offer and the file."""

    application: Application
    kind: OfferKind
    content: IO[bytes]
    file_kind: FileKind
    file_name: str
    size: int


def add_offer_letter(student: Student, letter: NewOfferLetter, by: Staff) -> Document:
    """Store the letter as a Pending Document linked to the Application, which becomes Offer.

    History records the letter, and the Stage follows (spec 55).

    Raises:
        NotWaitingForOfferError: The Application is not at Applied.
        TooManyDocumentsError: The Student already has the most Documents allowed.
        DocumentStorageError: The file couldn't be stored; nothing is changed.
    """
    with transaction.atomic():
        record_offer(student, letter.application.pk, letter.kind, by)
        document = upload_document(
            student,
            NewDocument(
                document_type=offer_letter_type(),
                files=(NewFile(letter.content, letter.file_kind, letter.file_name, letter.size),),
                application=letter.application,
            ),
            by,
        )
    logger.info(
        "Offer letter added: application_id=%s, document_id=%s, student_id=%s, by_staff_id=%s",
        letter.application.pk,
        document.pk,
        student.pk,
        by.pk,
    )
    return document


def offer_letter_type() -> DocumentType:
    """The Consultancy's "Offer letter" Document Type (any case), made if it isn't there yet."""
    return document_type_named(OFFER_LETTER_TYPE_NAME)
