"""Document Types the app itself keeps letters as, found by name or made the first time."""

from __future__ import annotations

from django.db import IntegrityError, transaction

from documents.models import DocumentType


def document_type_named(name: str) -> DocumentType:
    """The Consultancy's Document Type of this name (any case), made if it isn't there yet."""
    found = DocumentType.objects.filter(name__iexact=name).first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return DocumentType.objects.create(name=name)
    except IntegrityError:  # pragma: no cover  # made by someone else meanwhile
        return DocumentType.objects.get(name__iexact=name)
