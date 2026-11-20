from __future__ import annotations

from django.db import models

from documents.models import (
    CONTENT_TYPE_MAX_LENGTH,
    FILE_NAME_MAX_LENGTH,
    STORAGE_KEY_MAX_LENGTH,
    DocumentType,
    InTheBin,
)
from staff.models import STAFF_NAME_MAX_LENGTH, Staff


class DocumentExample(InTheBin):
    """One example file of a Document Type: what it should look like (spec 62-66).

    GLOSSARY: Document Example. Never a Student's Document: it is stored in its own folder under
    the Consultancy's private prefix, never on a checklist, and reached only through a short-lived
    signed link (ADR 0015). Replacing or removing one puts it in the Bin. A Document Type that
    still has examples, even in the Bin, is never removed (PROTECT).
    """

    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name="examples"
    )
    # The uploaded file's own name, cleaned, ending in the extension of what it really is.
    file_name = models.CharField(max_length=FILE_NAME_MAX_LENGTH)
    content_type = models.CharField(max_length=CONTENT_TYPE_MAX_LENGTH)
    size = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=STORAGE_KEY_MAX_LENGTH, unique=True)
    # None when a Platform Admin uploaded it during Support Access (issue 27).
    uploaded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # Who uploaded it, as they were named then.
    uploaded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta(InTheBin.Meta):
        ordering = ("uploaded_at", "pk")

    def __str__(self) -> str:
        return self.file_name
