"""Uploading a Visa's letter, which can set the Visa's result (spec 116; issue 77).

The letter is a Document of the "Visa letter" type, linked to the Visa. On a Visa at Applied the
letter says Granted or Refused, and the Status changes with it as it does by hand (the Stage and
the Chosen Offer follow); the file and the Status are saved in one transaction, or not at all. A
letter can also be added later to a Visa already Granted or Refused, leaving its Status alone.
Call inside the Consultancy's schema; who may do it is the view's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction

from applications.models import Visa
from applications.services import change_visa_status
from applications.statuses import VisaStatus
from documents.exceptions import VisaAnsweredError, VisaResultNeededError
from documents.models import Document
from documents.named_types import document_type_named
from documents.services import upload_document
from documents.types import NewDocument, NewFile
from staff.models import Staff
from students.models import Student

# The Document Type every visa letter is kept as; made the first time it is needed. Its usual
# Document Group is Visa (ticket 80 adds the groups).
VISA_LETTER_TYPE_NAME = "Visa letter"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewVisaLetter:
    """A checked visa letter: what it says (Granted or Refused, or None) and its file."""

    result: VisaStatus | None
    file: NewFile


def add_visa_letter(student: Student, visa_id: int, letter: NewVisaLetter, by: Staff) -> Document:
    """Store the letter as a Pending Document linked to the Visa, setting its result if waiting.

    Raises:
        Visa.DoesNotExist: The Student has no such Visa.
        VisaResultNeededError: The Visa is at Applied and the letter says neither result.
        VisaAnsweredError: The Visa already has the other result.
        TooManyDocumentsError: The Student already has the most Documents allowed.
        DocumentStorageError: The file couldn't be stored; nothing is changed.
    """
    with transaction.atomic():
        # The Student first, then the Visa: the order change_visa_status locks them in.
        locked = Student.objects.select_for_update().get(pk=student.pk)
        visa = Visa.objects.select_for_update().get(student=locked, pk=visa_id)
        current = VisaStatus(visa.status)
        result = "none"
        if current == VisaStatus.APPLIED:
            if letter.result is None:
                raise VisaResultNeededError
            change_visa_status(student, visa.pk, letter.result, by)
            result = letter.result.value
        elif letter.result not in {None, current}:
            raise VisaAnsweredError(current.label)
        document = upload_document(
            student,
            NewDocument(
                document_type=document_type_named(VISA_LETTER_TYPE_NAME),
                files=(letter.file,),
                visa=visa,
            ),
            by,
        )
    logger.info(
        "Visa letter added: visa_id=%s, document_id=%s, student_id=%s, result=%s, by_staff_id=%s",
        visa.pk,
        document.pk,
        student.pk,
        result,
        by.pk,
    )
    return document
