"""What the Document endpoints accept. The server never trusts the browser (issues README).

An upload is checked from its bytes: the browser's file name and type are never believed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.core.files.uploadedfile import UploadedFile
from django.db.models import Model
from django.http import QueryDict
from rest_framework import serializers

from applications.models import Application, Visa
from applications.statuses import OfferKind, VisaStatus
from config.serializers import RecordIdField, StrictSerializer, parse_record_id
from documents.files import (
    MAX_DOCUMENT_BYTES,
    MAX_FILES_AT_ONCE,
    MAX_UPLOAD_BYTES,
    SIGNATURE_BYTES,
    FileKind,
    detect_kind,
    safe_file_name,
)
from documents.groups import DocumentGroup
from documents.models import (
    DOCUMENT_TYPE_NAME_MAX_LENGTH,
    REJECTION_REASON_MAX_LENGTH,
    DocumentType,
)
from documents.offer_letters import NewOfferLetter
from documents.requirements import ListedType
from documents.services import MAX_DOCUMENT_TYPES
from documents.types import NewDocument, NewDocumentType, NewFile
from documents.verification import Verification
from documents.visa_letters import NewVisaLetter
from modules.serializers import StrictBooleanField
from staff.serializers import PlainTextField
from students.models import Student
from students.serializers import NoteField, StrictChoiceField

if TYPE_CHECKING:
    Upload = UploadedFile[bytes]
else:  # Not subscriptable at run time.
    Upload = UploadedFile

ENTER_NAME_MESSAGE = "Enter the document's name."
CHOOSE_TYPE_MESSAGE = "Choose a document type from the list."
CHOOSE_FILE_MESSAGE = "Choose a file to upload."
FILE_TOO_BIG_MESSAGE = "The file is too big. Upload at most 10 MB."
FILE_KIND_MESSAGE = "Upload a PDF, JPG or PNG file."
TOO_MANY_AT_ONCE_MESSAGE = f"Upload at most {MAX_FILES_AT_ONCE} files at once."
_UPLOAD_MB = MAX_UPLOAD_BYTES // (1024 * 1024)
TOO_BIG_TOGETHER_MESSAGE = (
    f"The files are too big together. Upload at most {_UPLOAD_MB} MB at once."
)
SEND_ONCE_MESSAGE = "Send this only once."
CHOOSE_APPLICATION_MESSAGE = "Choose one of this Student's applications."
CHOOSE_VISA_MESSAGE = "Choose one of this Student's visas."
ONE_LINK_MESSAGE = "Link it to an application or a visa, not both."
CHOOSE_KIND_MESSAGE = "Choose Conditional or Unconditional."
CHOOSE_RESULT_MESSAGE = "Choose Granted or Refused."
# What a visa letter can say (issue 77); Applied is never a result.
_VISA_RESULTS = [(s.value, s.label) for s in (VisaStatus.GRANTED, VisaStatus.REFUSED)]
REASON_NEEDED_MESSAGE = "Say why the document is rejected."
ONLY_REJECTED_HAS_A_REASON_MESSAGE = "Only a rejected document has a reason."
SCOPE_MINE = "mine"
SCOPE_EVERYONE = "everyone"
OPEN = "open"
DOWNLOAD = "download"
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "max_length")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


CHOOSE_GROUP_MESSAGE = "Choose Applying, Visa or Statements."


def _group_field(*, required: bool) -> StrictChoiceField:
    return StrictChoiceField(
        choices=DocumentGroup.choices,
        required=required,
        error_messages={
            "required": CHOOSE_GROUP_MESSAGE,
            "null": CHOOSE_GROUP_MESSAGE,
            "invalid_choice": CHOOSE_GROUP_MESSAGE,
        },
    )


class NewDocumentTypeSerializer(StrictSerializer[None]):
    """A new Document Type: its name, whether every Student needs it, and its usual group.

    Spec 63; the usual Document Group is Applying unless one is sent (issue 80).
    """

    name = PlainTextField(
        max_length=DOCUMENT_TYPE_NAME_MAX_LENGTH,
        error_messages={"required": ENTER_NAME_MESSAGE, "blank": ENTER_NAME_MESSAGE},
    )
    needed_by_everyone = StrictBooleanField()
    usual_group = _group_field(required=False)

    def to_new_type(self) -> NewDocumentType:
        """Return the validated name (trimmed), flag and usual group."""
        data = self.validated_data
        return NewDocumentType(
            name=data["name"],
            needed_by_everyone=data["needed_by_everyone"],
            usual_group=DocumentGroup(data.get("usual_group", DocumentGroup.APPLYING)),
        )


class NewCountryDocumentSerializer(StrictSerializer[None]):
    """A new Document Type made from a Country's list: its name and the list's group (spec 121a)."""

    name = PlainTextField(
        max_length=DOCUMENT_TYPE_NAME_MAX_LENGTH,
        error_messages={"required": ENTER_NAME_MESSAGE, "blank": ENTER_NAME_MESSAGE},
    )
    group = _group_field(required=True)

    def name_value(self) -> str:
        """The validated name (trimmed)."""
        return str(self.validated_data["name"])

    def group_value(self) -> DocumentGroup:
        """The Document Group of the list it goes on."""
        return DocumentGroup(self.validated_data["group"])


class DocumentTypeChangeSerializer(StrictSerializer[None]):
    """Move a Document Type onto or off every Student's checklist. Its name doesn't change."""

    needed_by_everyone = StrictBooleanField()

    def needed(self) -> bool:
        """Whether every Student now needs it."""
        return bool(self.validated_data["needed_by_everyone"])


class DocumentTypeField(serializers.Field[DocumentType, str, str, Any]):
    """A Document Type by its UUID (ADR 0027), sent as text in a form upload."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(CHOOSE_TYPE_MESSAGE))

    def to_internal_value(self, data: object) -> DocumentType:
        """Return the type; anything that isn't one of this Consultancy's is refused."""
        public_id = parse_record_id(data)
        found = (
            None if public_id is None else DocumentType.objects.filter(public_id=public_id).first()
        )
        if found is None:
            self.fail("invalid")
        return found

    def to_representation(self, value: DocumentType) -> str:
        """Return the UUID (unused: answers are built by hand)."""
        return str(value.public_id)


class StudentRecordField[M: Model](serializers.Field[M, str, str, Any]):
    """One of the uploading Student's own Applications or Visas, by UUID (spec 66). Optional.

    The Student comes from the serializer's context; anyone else's record is refused.
    """

    def __init__(self, model: type[M], message: str, *, required: bool = False) -> None:
        super().__init__(required=required, error_messages=_same_message(message))
        self.model = model

    def to_internal_value(self, data: object) -> M:
        """Return the record, if it is this Student's."""
        student: Student = self.context["student"]
        public_id = parse_record_id(data)
        found = (
            None
            if public_id is None
            else self.model._default_manager.filter(public_id=public_id, student=student).first()
        )
        if found is None:
            self.fail("invalid")
        return found

    def to_representation(self, value: M) -> str:
        """Return the UUID (unused: answers are built by hand)."""
        return str(value.serializable_value("public_id"))


class DocumentFileField(serializers.Field[Upload, Upload, None, Any]):
    """One uploaded file: a PDF, JPG or PNG (by its bytes), not empty, at most 10 MB."""

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(CHOOSE_FILE_MESSAGE))

    def to_internal_value(self, data: object) -> Upload:
        """Return the file once its size and first bytes are checked."""
        if not isinstance(data, UploadedFile) or not data.size:
            self.fail("invalid")
        if data.size > MAX_DOCUMENT_BYTES:
            raise serializers.ValidationError(FILE_TOO_BIG_MESSAGE)
        if _kind_of(data) is None:
            raise serializers.ValidationError(FILE_KIND_MESSAGE)
        return data

    def to_representation(self, value: Upload) -> None:
        """Never shown."""


def _kind_of(upload: Upload) -> FileKind | None:
    upload.seek(0)
    start = upload.read(SIGNATURE_BYTES)
    upload.seek(0)
    return detect_kind(start)


class DocumentFilesField(serializers.Field[list[Upload], list[Upload], None, Any]):
    """One or more files sent under the same name (issue 78), each checked like one file.

    At most MAX_FILES_AT_ONCE, and at most MAX_UPLOAD_BYTES together.
    """

    def __init__(self) -> None:
        super().__init__(error_messages=_same_message(CHOOSE_FILE_MESSAGE))
        self._one = DocumentFileField()

    def get_value(self, dictionary: Any) -> object:  # noqa: ANN401  # DRF's own signature
        """Every value sent under the field's name, from a form; a plain object's one value."""
        if isinstance(dictionary, QueryDict):
            return dictionary.getlist(str(self.field_name)) or serializers.empty
        return super().get_value(dictionary)

    def to_internal_value(self, data: object) -> list[Upload]:
        """Return the files once there are not too many, each is checked, and not too big."""
        sent = data if isinstance(data, list) else [data]
        if not sent:
            self.fail("required")
        if len(sent) > MAX_FILES_AT_ONCE:
            raise serializers.ValidationError(TOO_MANY_AT_ONCE_MESSAGE)
        uploads = [self._one.to_internal_value(one) for one in sent]
        if sum(int(upload.size or 0) for upload in uploads) > MAX_UPLOAD_BYTES:
            raise serializers.ValidationError(TOO_BIG_TOGETHER_MESSAGE)
        return uploads

    def to_representation(self, value: list[Upload]) -> None:
        """Never shown."""


def _new_files(uploads: list[Upload]) -> tuple[NewFile, ...]:
    """The checked files, in the order sent, each with the name it will be shown as."""
    files = []
    for upload in uploads:
        kind, file_name = _checked_file(upload)
        files.append(NewFile(upload, kind, file_name, int(upload.size or 0)))
    return tuple(files)


class _OnceOnlyForm(StrictSerializer[None]):
    """A form upload in which every field is sent once, but those named in `repeatable`."""

    repeatable: frozenset[str] = frozenset()

    def to_internal_value(self, data: object) -> dict[str, object]:
        """Refuse a field sent more than once, which a form allows."""
        if isinstance(data, QueryDict):
            repeated = {
                str(key): [SEND_ONCE_MESSAGE]
                for key in data
                if key not in self.repeatable and len(data.getlist(key)) > 1
            }
            if repeated:
                raise serializers.ValidationError(repeated)
        return super().to_internal_value(data)


def _checked_file(upload: Upload) -> tuple[FileKind, str]:
    """What the uploaded file really is, and the name it will be shown and downloaded as."""
    kind = _kind_of(upload)
    if kind is None:  # pragma: no cover  # checked by DocumentFileField
        raise serializers.ValidationError({"file": [FILE_KIND_MESSAGE]})
    return kind, safe_file_name(upload.name or "", kind)


class OfferLetterSerializer(_OnceOnlyForm):
    """An Offer Letter (a form with one file): which Application, the kind of offer and the file.

    The Application must be the Student's own, so the serializer needs the Student in its
    context; the service checks it is waiting for an offer (spec 114).
    """

    application = StudentRecordField(Application, CHOOSE_APPLICATION_MESSAGE, required=True)
    kind = StrictChoiceField(
        choices=OfferKind.choices,
        error_messages={
            **_same_message(CHOOSE_KIND_MESSAGE),
            "invalid_choice": CHOOSE_KIND_MESSAGE,
        },
    )
    file = DocumentFileField()

    def to_new_offer_letter(self) -> NewOfferLetter:
        """Return the checked letter."""
        upload: Upload = self.validated_data["file"]
        file_kind, file_name = _checked_file(upload)
        return NewOfferLetter(
            application=self.validated_data["application"],
            kind=OfferKind(self.validated_data["kind"]),
            content=upload,
            file_kind=file_kind,
            file_name=file_name,
            size=int(upload.size or 0),
        )


class VisaLetterSerializer(_OnceOnlyForm):
    """A visa letter (a form with one file) and what it says: Granted or Refused (spec 116).

    The result may be left out; the service decides whether this Visa needs one.
    """

    result = StrictChoiceField(
        choices=_VISA_RESULTS,
        required=False,
        error_messages={
            **_same_message(CHOOSE_RESULT_MESSAGE),
            "invalid_choice": CHOOSE_RESULT_MESSAGE,
        },
    )
    file = DocumentFileField()

    def to_new_visa_letter(self) -> NewVisaLetter:
        """Return the checked letter."""
        upload: Upload = self.validated_data["file"]
        file_kind, file_name = _checked_file(upload)
        result = self.validated_data.get("result")
        return NewVisaLetter(
            result=None if result is None else VisaStatus(result),
            file=NewFile(upload, file_kind, file_name, int(upload.size or 0)),
        )


class DocumentUploadSerializer(_OnceOnlyForm):
    """A Document upload (a form): its type, its files (`file`, sent once per file) and a link.

    The link is to one of the Student's Applications or Visas (spec 65-66), so the serializer
    needs the Student in its context.
    """

    repeatable = frozenset({"file"})
    document_type = DocumentTypeField()
    file = DocumentFilesField()
    application = StudentRecordField(Application, CHOOSE_APPLICATION_MESSAGE)
    visa = StudentRecordField(Visa, CHOOSE_VISA_MESSAGE)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """A Document is linked to an Application or a Visa, never both."""
        if "application" in attrs and "visa" in attrs:
            raise serializers.ValidationError(ONE_LINK_MESSAGE)
        return attrs

    def to_new_document(self) -> NewDocument:
        """Return the checked upload, each file with the name it will be shown as."""
        return NewDocument(
            document_type=self.validated_data["document_type"],
            files=_new_files(self.validated_data["file"]),
            application=self.validated_data.get("application"),
            visa=self.validated_data.get("visa"),
        )


class NewFilesSerializer(_OnceOnlyForm):
    """Files added to a Document (a form): `file`, sent once per file (issue 78)."""

    repeatable = frozenset({"file"})
    file = DocumentFilesField()

    def to_new_files(self) -> tuple[NewFile, ...]:
        """Return the checked files, each with the name it will be shown as."""
        return _new_files(self.validated_data["file"])


class VerificationSerializer(StrictSerializer[None]):
    """Mark a Document correct, or Rejected with the reason (spec 68)."""

    verification = StrictChoiceField(
        choices=(
            (Verification.VERIFIED.value, Verification.VERIFIED.label),
            (Verification.REJECTED.value, Verification.REJECTED.label),
        )
    )
    reason = NoteField(max_length=REJECTION_REASON_MAX_LENGTH)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """A reason is needed for, and only for, a rejection."""
        reason = attrs.get("reason", "")
        is_rejected = attrs["verification"] == Verification.REJECTED
        if is_rejected and not reason:
            raise serializers.ValidationError({"reason": [REASON_NEEDED_MESSAGE]})
        if reason and not is_rejected:
            raise serializers.ValidationError({"reason": [ONLY_REJECTED_HAS_A_REASON_MESSAGE]})
        return attrs

    def verification_value(self) -> Verification:
        """Correct or Rejected."""
        return Verification(self.validated_data["verification"])

    def reason_value(self) -> str:
        """Why it was rejected (trimmed); empty when marked correct."""
        reason: str = self.validated_data.get("reason", "")
        return reason


class DocumentLinkQuerySerializer(StrictSerializer[None]):
    """Whether the link opens the file in the browser or saves it."""

    action = StrictChoiceField(choices=((OPEN, OPEN), (DOWNLOAD, DOWNLOAD)), required=False)

    def as_attachment(self) -> bool:
        """True to save the file; opening it is the default."""
        action: str = self.validated_data.get("action", OPEN)
        return action == DOWNLOAD


class PaperworkQuerySerializer(StrictSerializer[None]):
    """The Documents page: whose Students, and which page."""

    scope = StrictChoiceField(
        choices=((SCOPE_MINE, SCOPE_MINE), (SCOPE_EVERYONE, SCOPE_EVERYONE)), required=False
    )
    # "true": only Students with a Document waiting to be checked (the Home's link, issue 52).
    waiting = StrictChoiceField(choices=(("true", "true"), ("false", "false")), required=False)
    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def only_waiting(self) -> bool:
        """Only Students with a Document waiting to be checked; everyone by default."""
        return bool(self.validated_data.get("waiting") == "true")

    def only_mine(self) -> bool:
        """Only the Students assigned to the viewer; everyone's by default."""
        scope: str = self.validated_data.get("scope", SCOPE_EVERYONE)
        return scope == SCOPE_MINE

    def page_number(self) -> int:
        """The page asked for; the first by default."""
        return int(self.validated_data.get("page", 1))


# The longest name a document picker adds (spec 63c); Configuration allows the model's maximum.
PICKED_NAME_MAX_LENGTH = 80
CHOOSE_TYPES_MESSAGE = "Choose documents from the list."
CHOOSE_EACH_ONCE_MESSAGE = "Choose each document only once."
# Only this many items are looked at before a list too long is refused.
_LIST_LIMIT = MAX_DOCUMENT_TYPES + 1


def _everyone_needs_message(name: str) -> str:
    return f"{name} is already needed by every Student."


class DocumentTypeListField(serializers.Field[list[DocumentType], list[str], list[str], Any]):
    """Document Types by UUID, each once, none that every Student needs (issue 40).

    The order kept is the Consultancy's own (oldest type first), whatever order was sent.
    """

    def __init__(self, *, required: bool = True) -> None:
        super().__init__(required=required, error_messages={"invalid": CHOOSE_TYPES_MESSAGE})

    def to_internal_value(self, data: object) -> list[DocumentType]:
        """Return the types, or refuse the whole list."""
        ids = self._ids(data)
        found = list(DocumentType.objects.filter(public_id__in=ids).order_by("pk"))
        if len(found) != len(ids):
            self.fail("invalid")
        needed = next((t for t in found if t.needed_by_everyone), None)
        if needed is not None:
            raise serializers.ValidationError(_everyone_needs_message(needed.name))
        return found

    def _ids(self, data: object) -> list[UUID]:
        """A list of UUIDs, each once, no longer than an office's types can be."""
        if not isinstance(data, list) or not all(_is_id(item) for item in data[:_LIST_LIMIT]):
            self.fail("invalid")
        if len(data) > MAX_DOCUMENT_TYPES:
            raise serializers.ValidationError(f"Choose at most {MAX_DOCUMENT_TYPES} documents.")
        ids = [UUID(item) for item in data]
        if len(set(ids)) != len(ids):
            raise serializers.ValidationError(CHOOSE_EACH_ONCE_MESSAGE)
        return ids

    def to_representation(self, value: list[DocumentType]) -> list[str]:
        """Return the UUIDs (unused: answers are built by hand)."""
        return [str(document_type.public_id) for document_type in value]


def _is_id(item: object) -> bool:
    return parse_record_id(item) is not None


class DocumentTypeListSerializer(StrictSerializer[None]):
    """The whole list of extra Document Types a University, Course or Student asks for."""

    document_types = DocumentTypeListField()

    def types(self) -> list[DocumentType]:
        """The validated types, oldest first."""
        found: list[DocumentType] = self.validated_data["document_types"]
        return found


class ListedTypeSerializer(StrictSerializer[None]):
    """One row of a Country's list: a Document Type by UUID and the group it goes in."""

    document_type = RecordIdField(CHOOSE_TYPES_MESSAGE)
    group = _group_field(required=True)


class CountryListSerializer(StrictSerializer[None]):
    """The whole document list of one Country (spec 121; issue 80): each type once, in a group."""

    documents = ListedTypeSerializer(many=True)

    def to_internal_value(self, data: object) -> dict[str, object]:
        """Refuse a list longer than an office's types can be, before looking at its rows."""
        if isinstance(data, dict):
            rows = data.get("documents")
            if isinstance(rows, list) and len(rows) > MAX_DOCUMENT_TYPES:
                message = f"Choose at most {MAX_DOCUMENT_TYPES} documents."
                raise serializers.ValidationError({"documents": [message]})
        return super().to_internal_value(data)

    def validate_documents(self, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        """Refuse a type sent twice; the rest is checked against the office's types."""
        ids = [row["document_type"] for row in rows]
        if len(set(ids)) != len(ids):
            raise serializers.ValidationError(CHOOSE_EACH_ONCE_MESSAGE)
        return rows

    def listed(self) -> list[ListedType]:
        """The rows as Document Types of this office, oldest type first.

        Raises:
            serializers.ValidationError: A type isn't one of this office's, or every Student
                needs it.
        """
        rows: list[dict[str, Any]] = self.validated_data["documents"]
        groups = {row["document_type"]: DocumentGroup(row["group"]) for row in rows}
        found = list(DocumentType.objects.filter(public_id__in=groups).order_by("pk"))
        if len(found) != len(groups):
            raise serializers.ValidationError({"documents": [CHOOSE_TYPES_MESSAGE]})
        needed = next((t for t in found if t.needed_by_everyone), None)
        if needed is not None:
            raise serializers.ValidationError({"documents": [_everyone_needs_message(needed.name)]})
        return [ListedType(t, groups[t.public_id]) for t in found]


class PickedTypeSerializer(StrictSerializer[None]):
    """A name typed into a document picker: added as asked for only when needed (spec 63c)."""

    name = PlainTextField(
        max_length=PICKED_NAME_MAX_LENGTH,
        error_messages={"required": ENTER_NAME_MESSAGE, "blank": ENTER_NAME_MESSAGE},
    )

    def to_new_type(self) -> NewDocumentType:
        """Return the validated name (trimmed), never needed by everyone."""
        return NewDocumentType(name=self.validated_data["name"], needed_by_everyone=False)


# Why something goes to the Bin (issue 79): a quick pick, or a few words of the person's own.
BIN_REASON_NEEDED_MESSAGE = "Say why it should be deleted."
BIN_REASON_MAX_CHARACTERS = 200


class BinReasonSerializer(StrictSerializer[None]):
    """Why a Document or a file should be deleted: one line of text, at most 200 characters."""

    reason = PlainTextField(
        max_length=BIN_REASON_MAX_CHARACTERS,
        error_messages={
            "required": BIN_REASON_NEEDED_MESSAGE,
            "null": BIN_REASON_NEEDED_MESSAGE,
            "blank": BIN_REASON_NEEDED_MESSAGE,
        },
    )

    def reason_value(self) -> str:
        """The reason, trimmed."""
        reason: str = self.validated_data["reason"]
        return reason


class NothingSerializer(StrictSerializer[None]):
    """An action that takes nothing: anything sent is refused, never stored."""


class BinQuerySerializer(StrictSerializer[None]):
    """The Bin tab: which page."""

    page = serializers.IntegerField(min_value=1, max_value=1_000_000, required=False)

    def page_number(self) -> int:
        """The page asked for; the first by default."""
        return int(self.validated_data.get("page", 1))
