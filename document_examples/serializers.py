"""What the Document Example endpoints accept. The server never trusts the browser (spec 99)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from config.serializers import StrictSerializer
from document_examples.types import NewExample
from documents.files import SIGNATURE_BYTES, detect_kind, safe_file_name
from documents.models import INSTRUCTION_MAX_LENGTH
from documents.serializers import Upload, _OnceOnlyForm
from staff.serializers import PlainTextField

if TYPE_CHECKING:
    from django.utils.functional import _StrPromise

# The largest example file kept; emails carry at most 10 MB in all (spec 62).
MAX_EXAMPLE_BYTES = 5 * 1024 * 1024
CHOOSE_FILE_MESSAGE = "Choose a PDF, JPEG or PNG file."
WRONG_KIND_MESSAGE = "An example must be a PDF, JPEG or PNG file. That one isn't."
TOO_BIG_MESSAGE = "The file is too big. An example can be at most 5 MB."


class ExampleFileField(serializers.Field[Upload, Upload, None, Any]):
    """One uploaded file that is a PDF, JPEG or PNG by its bytes, not empty, at most 5 MB."""

    def __init__(self) -> None:
        messages: dict[str, str | _StrPromise] = dict.fromkeys(
            ("required", "null", "invalid"), CHOOSE_FILE_MESSAGE
        )
        super().__init__(error_messages=messages)

    def to_internal_value(self, data: object) -> Upload:
        """Return the file once its size and first bytes are checked."""
        if not isinstance(data, UploadedFile) or not data.size:
            self.fail("invalid")
        if data.size > MAX_EXAMPLE_BYTES:
            raise serializers.ValidationError(TOO_BIG_MESSAGE)
        data.seek(0)
        start = data.read(SIGNATURE_BYTES)
        data.seek(0)
        if detect_kind(start) is None:
            raise serializers.ValidationError(WRONG_KIND_MESSAGE)
        return data

    def to_representation(self, value: Upload) -> None:
        """Never shown."""


class NewExampleSerializer(_OnceOnlyForm):
    """A form with one example file."""

    file = ExampleFileField()

    def to_new_example(self) -> NewExample:
        """The checked file, for `add_example` or `replace_example`."""
        upload: Upload = self.validated_data["file"]
        upload.seek(0)
        kind = detect_kind(upload.read(SIGNATURE_BYTES))
        upload.seek(0)
        assert kind is not None  # noqa: S101  # checked by ExampleFileField
        return NewExample(
            content=upload,
            kind=kind,
            file_name=safe_file_name(upload.name or "", kind),
            size=int(upload.size or 0),
        )


class InstructionSerializer(StrictSerializer[None]):
    """How the Document should look, in one line of up to 500 characters; "" clears it."""

    instruction = PlainTextField(max_length=INSTRUCTION_MAX_LENGTH, allow_blank=True)

    def instruction_value(self) -> str:
        """The checked instruction."""
        instruction: str = self.validated_data["instruction"]
        return instruction
