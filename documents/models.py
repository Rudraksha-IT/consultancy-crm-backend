from __future__ import annotations

from django.db import models
from django.db.models.functions import Lower

from applications.models import Application, Visa
from catalogue.models import Course
from documents.groups import DocumentGroup
from documents.verification import Verification
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import CHOICE_MAX_LENGTH, Country, Student

DOCUMENT_TYPE_NAME_MAX_LENGTH = 100
FILE_NAME_MAX_LENGTH = 150
CONTENT_TYPE_MAX_LENGTH = 50
STORAGE_KEY_MAX_LENGTH = 300
REJECTION_REASON_MAX_LENGTH = 500
# Why a Document or a file was put in the Bin (a quick pick or a few words; issue 79).
BIN_REASON_MAX_LENGTH = 200


class InTheBin(models.Model):
    """Who put a Document or a file in the Bin, when and why (spec 118; issue 79).

    Empty while it is in use. A Document or file in the Bin can't be opened, checked or used;
    only an Admin opens it, from the Bin tab, and restores it or deletes it for good.
    """

    binned_at = models.DateTimeField(null=True, blank=True)
    # None for a Platform Admin during Support Access, or once it is out of the Bin.
    binned_by = models.ForeignKey(
        Staff, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Who put it there, as they were named then (a Platform Admin's name is marked).
    binned_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, blank=True, default="")
    bin_reason = models.CharField(max_length=BIN_REASON_MAX_LENGTH, blank=True, default="")

    class Meta:
        abstract = True

    @property
    def is_in_bin(self) -> bool:
        """Whether it is waiting in the Bin."""
        return self.binned_at is not None


class DocumentType(models.Model):
    """A kind of Document the Consultancy expects from Students (spec 63).

    Needed by every Student, or asked for only when needed (by a Country's list, a Course or one
    Student; issues 40, 80). It sits in its usual Document Group unless a Country's list puts it
    in another.
    """

    name = models.CharField(max_length=DOCUMENT_TYPE_NAME_MAX_LENGTH)
    needed_by_everyone = models.BooleanField(default=False)
    usual_group = models.CharField(
        max_length=CHOICE_MAX_LENGTH, choices=DocumentGroup.choices, default=DocumentGroup.APPLYING
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("pk",)
        constraints = (models.UniqueConstraint(Lower("name"), name="documents_type_unique_name"),)

    def __str__(self) -> str:
        return self.name


class Document(InTheBin):
    """One Document of a Document Type belonging to a Student: one or more files (spec 65, 117).

    Verification is one judgment for all its files together (spec 68). A Student may have
    several of one type: the newest decides the checklist. It may be linked to one of the
    Student's Applications or Visas, never both (spec 66).
    """

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="documents")
    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name="documents"
    )
    verification = models.CharField(
        max_length=CHOICE_MAX_LENGTH,
        choices=Verification.choices,
        default=Verification.PENDING,
    )
    # Why it was Rejected (spec 68); empty unless Rejected.
    rejection_reason = models.TextField(
        max_length=REJECTION_REASON_MAX_LENGTH, blank=True, default=""
    )
    application = models.ForeignKey(
        Application, on_delete=models.PROTECT, null=True, blank=True, related_name="documents"
    )
    visa = models.ForeignKey(
        Visa, on_delete=models.PROTECT, null=True, blank=True, related_name="documents"
    )
    # None when a Platform Admin uploaded it during Support Access (issue 27).
    uploaded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # Who uploaded it, as they were named then.
    uploaded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta(InTheBin.Meta):
        ordering = ("pk",)
        constraints = (
            models.CheckConstraint(
                condition=models.Q(application__isnull=True) | models.Q(visa__isnull=True),
                name="documents_linked_to_one_record_at_most",
            ),
        )

    def __str__(self) -> str:
        return str(self.pk)


class DocumentFile(InTheBin):
    """One file of a Document, such as one page of a Transcript (spec 117; issue 78).

    The file itself is in private storage under the Consultancy's own prefix (`storage_key`); it
    is reached only through a short-lived signed link issued after a permission check (spec 70).
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="files")
    # The name shown and used for downloads; its extension always matches the real file type.
    file_name = models.CharField(max_length=FILE_NAME_MAX_LENGTH)
    content_type = models.CharField(max_length=CONTENT_TYPE_MAX_LENGTH)
    size = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=STORAGE_KEY_MAX_LENGTH, unique=True)
    # None when a Platform Admin added it during Support Access (issue 27).
    uploaded_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    # Who added it, as they were named then.
    uploaded_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta(InTheBin.Meta):
        ordering = ("pk",)

    def __str__(self) -> str:
        return self.file_name


class DocumentRequirement(models.Model):
    """A Document Type asked for by one Country's list, one Course or one Student (spec 63a, 121).

    On top of the types every Student needs (spec 64). A Country's row is placed in a Document
    Group; a Course's or a Student's uses the type's usual group. University lists were removed
    (issue 80).
    """

    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name="requirements"
    )
    # Empty unless this is a row of a Country's list.
    country = models.CharField(
        max_length=CHOICE_MAX_LENGTH, choices=Country.choices, blank=True, default=""
    )
    # Where the Country's list puts it; empty for a Course's or a Student's row.
    group = models.CharField(
        max_length=CHOICE_MAX_LENGTH, choices=DocumentGroup.choices, blank=True, default=""
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_requirements",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_requirements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("document_type_id", "pk")
        constraints = (
            models.CheckConstraint(
                condition=(
                    (
                        ~models.Q(country="")
                        & ~models.Q(group="")
                        & models.Q(course__isnull=True, student__isnull=True)
                    )
                    | models.Q(country="", group="", course__isnull=False, student__isnull=True)
                    | models.Q(country="", group="", course__isnull=True, student__isnull=False)
                ),
                name="documents_requirement_for_exactly_one",
            ),
            models.UniqueConstraint(
                "document_type",
                "country",
                condition=~models.Q(country=""),
                name="documents_requirement_unique_per_country",
            ),
            models.UniqueConstraint(
                "document_type",
                "course",
                condition=models.Q(course__isnull=False),
                name="documents_requirement_unique_per_course",
            ),
            models.UniqueConstraint(
                "document_type",
                "student",
                condition=models.Q(student__isnull=False),
                name="documents_requirement_unique_per_student",
            ),
        )

    def __str__(self) -> str:
        return str(self.document_type_id)
