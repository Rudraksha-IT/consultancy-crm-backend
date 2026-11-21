"""What the Course File endpoints accept. The server never trusts the browser (spec 99)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from config.serializers import StrictSerializer
from course_files.models import COURSE_FILE_NAME_MAX_LENGTH
from course_files.types import NewCourseFile
from documents.files import PDF, SIGNATURE_BYTES, detect_kind, safe_file_name
from documents.serializers import Upload, _OnceOnlyForm
from staff.serializers import PlainTextField

if TYPE_CHECKING:
    from django.utils.functional import _StrPromise

# The largest brochure kept; emails carry at most 10 MB in all (spec 54).
MAX_COURSE_FILE_BYTES = 5 * 1024 * 1024
CHOOSE_PDF_MESSAGE = "Choose a PDF file to upload."
NOT_A_PDF_MESSAGE = "Only PDF files can be Course Files. That one isn't a PDF."
TOO_BIG_MESSAGE = "The file is too big. A Course File can be at most 5 MB."
NAME_MESSAGE = "Give it a name."


def _name_field() -> PlainTextField:
    return PlainTextField(
        max_length=COURSE_FILE_NAME_MAX_LENGTH,
        error_messages={"required": NAME_MESSAGE, "blank": NAME_MESSAGE, "null": NAME_MESSAGE},
    )


class PdfField(serializers.Field[Upload, Upload, None, Any]):
    """One uploaded file that is a real PDF by its bytes, not empty, at most 5 MB."""

    def __init__(self) -> None:
        messages: dict[str, str | _StrPromise] = dict.fromkeys(
            ("required", "null", "invalid"), CHOOSE_PDF_MESSAGE
        )
        super().__init__(error_messages=messages)

    def to_internal_value(self, data: object) -> Upload:
        """Return the file once its size and first bytes are checked."""
        if not isinstance(data, UploadedFile) or not data.size:
            self.fail("invalid")
        if data.size > MAX_COURSE_FILE_BYTES:
            raise serializers.ValidationError(TOO_BIG_MESSAGE)
        data.seek(0)
        start = data.read(SIGNATURE_BYTES)
        data.seek(0)
        if detect_kind(start) is not PDF:
            raise serializers.ValidationError(NOT_A_PDF_MESSAGE)
        return data

    def to_representation(self, value: Upload) -> None:
        """Never shown."""


class NewCourseFileSerializer(_OnceOnlyForm):
    """A form with the PDF and the name Staff will see."""

    file = PdfField()
    name = _name_field()

    def to_new_course_file(self) -> NewCourseFile:
        """The checked file, for `add_course_file`."""
        upload: Upload = self.validated_data["file"]
        return NewCourseFile(
            content=upload,
            name=self.validated_data["name"],
            file_name=safe_file_name(upload.name or "", PDF),
            size=int(upload.size or 0),
        )


class CourseFileRenameSerializer(StrictSerializer[None]):
    """A new name to show."""

    name = _name_field()

    def name_value(self) -> str:
        """The checked name."""
        name: str = self.validated_data["name"]
        return name
