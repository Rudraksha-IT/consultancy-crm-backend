"""REST endpoints for Document Types, a Student's Documents and the Documents page (spec 63-70)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from django.core.paginator import EmptyPage, Paginator
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import PAPERWORK_ROLES_MESSAGE, StudentAction, can_act_on_student, why_cannot
from catalogue.answers import course_answer
from catalogue.models import Course
from catalogue.permissions import CanChangeCatalogue, CanReadCatalogue
from catalogue.services import course_by_id
from course_files.answers import binned_course_file_answer
from course_files.models import CourseFile
from course_files.services import binned_course_files
from documents.answers import (
    bin_item_answer,
    checklist_answer,
    configured_type_answer,
    country_list_answer,
    document_type_answer,
    paperwork_answer,
    paperwork_totals,
)
from documents.bin import (
    BinItem,
    BinTarget,
    bin_items,
    delete_for_good,
    file_in_bin,
    put_in_bin,
    restore,
    take_out,
)
from documents.exceptions import (
    AlreadyCheckedError,
    AlreadyInBinError,
    AlreadyUploadedError,
    DocumentInBinError,
    DocumentStorageError,
    DocumentTypeInUseError,
    DuplicateDocumentTypeError,
    EveryoneNeedsTypeError,
    NotAskedError,
    NotInBinError,
    NotNewestCopyError,
    NotWhoPutItInError,
    OnlyFileError,
    RejectedNeedsNewCopyError,
    TooManyDocumentsError,
    TooManyDocumentTypesError,
    TooManyFilesError,
)
from documents.models import Document, DocumentFile, DocumentType
from documents.requirements import (
    LISTED_COUNTRIES,
    add_new_type_to_country_list,
    configured_types,
    country_lists,
    drop_student_requirement,
    is_on_country_list,
    remove_document_type,
    set_country_requirements,
    set_course_requirements,
    set_student_requirements,
)
from documents.serializers import (
    BinQuerySerializer,
    BinReasonSerializer,
    CountryListSerializer,
    DocumentLinkQuerySerializer,
    DocumentTypeChangeSerializer,
    DocumentTypeListSerializer,
    DocumentUploadSerializer,
    NewCountryDocumentSerializer,
    NewDocumentTypeSerializer,
    NewFilesSerializer,
    NothingSerializer,
    PaperworkQuerySerializer,
    PickedTypeSerializer,
    VerificationSerializer,
)
from documents.services import (
    MAX_DOCUMENT_TYPES,
    MAX_DOCUMENTS_PER_STUDENT,
    MAX_FILES_PER_DOCUMENT,
    add_document_type,
    add_files,
    check_document,
    checklist_of,
    document_link,
    document_types,
    paperwork,
    set_needed_by_everyone,
    signed_file_link,
    upload_document,
    with_waiting_documents,
)
from documents.storage import SIGNED_LINK_SECONDS
from documents.types import NewDocumentType
from staff.actors import is_platform_visitor
from staff.context import get_request_staff
from staff.models import Role, Staff
from staff.permissions import IsAdmin
from students.models import Country, Student
from students.permissions import CanSeeStudents

# Students per page on the Documents page.
PAPERWORK_PER_PAGE = 25
DUPLICATE_TYPE_MESSAGE = "There is already a document with this name."
TOO_MANY_TYPES_MESSAGE = f"An office can have at most {MAX_DOCUMENT_TYPES} document types."
TOO_MANY_DOCUMENTS_MESSAGE = f"A Student can have at most {MAX_DOCUMENTS_PER_STUDENT} documents."
NOT_STORED_MESSAGE = "We couldn't store the file. Try again."
ALREADY_CHECKED_MESSAGE = "This document has already been checked."
NOT_NEWEST_MESSAGE = "A newer copy of this document has been uploaded."
TYPE_IN_USE_MESSAGE = "This document is asked for or has files, so it can't be removed."
EVERYONE_NEEDS_TYPE_MESSAGE = "Every Student needs this document, so it can't be removed."
TOO_MANY_FILES_MESSAGE = f"A document can have at most {MAX_FILES_PER_DOCUMENT} files."
REJECTED_NEEDS_NEW_COPY_MESSAGE = (
    "This document was rejected. Upload a new copy instead of adding a file."
)


# The Bin (spec 118, 119; issue 79).
BIN_PER_PAGE = 25
ADMINS_DELETE_MESSAGE = "As an Admin, delete it for good instead."
PUT_IT_IN_THE_BIN_MESSAGE = "Put it in the Bin instead."
ONLY_ADMIN_MESSAGE = "Only an Admin can do this."
ALREADY_IN_BIN_MESSAGE = "This is already in the Bin."
NOT_IN_BIN_MESSAGE = "This isn't in the Bin."
ONLY_FILE_MESSAGE = "This is the document's only file. Delete the document instead."
IN_BIN_MESSAGE = "This document is in the Bin."
ONLY_WHO_PUT_IT_MESSAGE = "Only the person who put it in the Bin can take it out."
NOT_DELETED_MESSAGE = "We couldn't delete the files. Try again."


def _already_uploaded_message(name: str) -> str:
    return f"A file is already uploaded for {name}, so it stays on the checklist."


def _student(student_id: int) -> Student:
    try:
        return Student.objects.select_related("assigned_counsellor").get(pk=student_id)
    except Student.DoesNotExist as error:
        raise NotFound from error


def _changeable_student(request: Request, student_id: int) -> Student:
    """The Student, if the Staff member may change their Documents; else 404/403."""
    student = _student(student_id)
    reason = why_cannot(get_request_staff(request), StudentAction.CHANGE_DOCUMENTS, student)
    if reason is not None:
        raise PermissionDenied(reason)
    return student


def _refused(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _checklist(student: Student, viewer: Staff, code: int = status.HTTP_200_OK) -> Response:
    can_change = can_act_on_student(viewer, StudentAction.CHANGE_DOCUMENTS, student)
    answer = checklist_answer(
        checklist_of(student),
        can_change=can_change,
        deletes_for_good=viewer.role == Role.ADMIN and not is_platform_visitor(viewer),
        viewer_id=None if is_platform_visitor(viewer) else viewer.pk,
    )
    return Response(answer, status=code)


class DocumentTypeListView(APIView):
    """The Consultancy's Document Types (GET) and adding one (POST); Configuration, Admin only."""

    permission_classes = (IsAuthenticated, IsAdmin)

    def get(self, request: Request) -> Response:
        """Return every Document Type, oldest first, with where each is used."""
        return Response({"results": [configured_type_answer(t) for t in configured_types()]})

    def post(self, request: Request) -> Response:
        """Add a Document Type and return it as stored."""
        serializer = NewDocumentTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _added_type(request, serializer.to_new_type(), configured=True)


def _added_type(request: Request, details: NewDocumentType, *, configured: bool) -> Response:
    """Add the Document Type, answering as Configuration or a document picker does."""
    try:
        added = add_document_type(details, by=get_request_staff(request))
    except DuplicateDocumentTypeError:
        # expected refusal, answered with its status; the request line logs it
        return Response({"name": [DUPLICATE_TYPE_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)
    except TooManyDocumentTypesError:
        # expected refusal, answered with its status; the request line logs it
        return _refused(TOO_MANY_TYPES_MESSAGE)
    answer = (
        configured_type_answer(configured_types().get(pk=added.pk))
        if configured
        else document_type_answer(added)
    )
    return Response(answer, status=status.HTTP_201_CREATED)


class DocumentTypeView(APIView):
    """Change one Document Type in Configuration (Admin only).

    Move it onto or off every Student's checklist (PATCH), or remove an unused one (DELETE).
    """

    permission_classes = (IsAuthenticated, IsAdmin)

    def patch(self, request: Request, type_id: int) -> Response:
        """Change whether every Student needs it, and return it as stored."""
        serializer = DocumentTypeChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            changed = set_needed_by_everyone(
                type_id, serializer.needed(), by=get_request_staff(request)
            )
        except DocumentType.DoesNotExist as error:
            raise NotFound from error
        return Response(configured_type_answer(configured_types().get(pk=changed.pk)))

    def delete(self, request: Request, type_id: int) -> Response:
        """Remove the type if nothing asks for it and it has no files."""
        try:
            remove_document_type(type_id, by=get_request_staff(request))
        except DocumentType.DoesNotExist as error:
            raise NotFound from error
        except EveryoneNeedsTypeError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(EVERYONE_NEEDS_TYPE_MESSAGE)
        except DocumentTypeInUseError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(TYPE_IN_USE_MESSAGE)
        return Response(status=status.HTTP_204_NO_CONTENT)


class StudentDocumentsView(APIView):
    """A Student's checklist (GET; anyone who sees Students) and uploading to it (POST)."""

    permission_classes = (IsAuthenticated, CanSeeStudents)
    # Uploads are a form with one file; nothing else is read.
    parser_classes = (MultiPartParser,)

    def get(self, request: Request, student_id: int) -> Response:
        """Return the Student's checklist."""
        return _checklist(_student(student_id), get_request_staff(request))

    def post(self, request: Request, student_id: int) -> Response:
        """Store the file as Pending Verification and return the checklist as it is now."""
        student = _changeable_student(request, student_id)
        staff = get_request_staff(request)
        serializer = DocumentUploadSerializer(data=request.data, context={"student": student})
        serializer.is_valid(raise_exception=True)
        try:
            upload_document(student, serializer.to_new_document(), by=staff)
        except TooManyDocumentsError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(TOO_MANY_DOCUMENTS_MESSAGE)
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return _not_stored()
        return _checklist(student, staff, status.HTTP_201_CREATED)


def _not_stored() -> Response:
    return Response({"detail": NOT_STORED_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


# Why files can't be added to a Document, by the service's refusal.
_ADD_FILES_REFUSALS: dict[type[Exception], str] = {
    DocumentInBinError: IN_BIN_MESSAGE,
    RejectedNeedsNewCopyError: REJECTED_NEEDS_NEW_COPY_MESSAGE,
    NotNewestCopyError: NOT_NEWEST_MESSAGE,
    TooManyFilesError: TOO_MANY_FILES_MESSAGE,
}


class DocumentFilesView(APIView):
    """Add one or more files to one of a Student's Documents (POST; spec 117, issue 78)."""

    permission_classes = (IsAuthenticated, CanSeeStudents)
    # A form with the files only; nothing else is read.
    parser_classes = (MultiPartParser,)

    def post(self, request: Request, student_id: int, document_id: int) -> Response:
        """Store the files and return the checklist as it is now."""
        student = _changeable_student(request, student_id)
        staff = get_request_staff(request)
        serializer = NewFilesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            add_files(student, document_id, serializer.to_new_files(), by=staff)
        except Document.DoesNotExist as error:
            raise NotFound from error
        except (
            DocumentInBinError,
            RejectedNeedsNewCopyError,
            NotNewestCopyError,
            TooManyFilesError,
        ) as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(_ADD_FILES_REFUSALS[type(error)])
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return _not_stored()
        return _checklist(student, staff, status.HTTP_201_CREATED)


class DocumentVerificationView(APIView):
    """Mark one of a Student's Documents correct, or Rejected with a reason (POST; spec 68)."""

    permission_classes = (IsAuthenticated, CanSeeStudents)
    parser_classes = (JSONParser,)

    def post(self, request: Request, student_id: int, document_id: int) -> Response:
        """Record the judgment and return the checklist as it is now."""
        student = _changeable_student(request, student_id)
        staff = get_request_staff(request)
        serializer = VerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            check_document(
                student,
                document_id,
                serializer.verification_value(),
                by=staff,
                reason=serializer.reason_value(),
            )
        except Document.DoesNotExist as error:
            raise NotFound from error
        except AlreadyCheckedError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(ALREADY_CHECKED_MESSAGE)
        except DocumentInBinError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(IN_BIN_MESSAGE)
        except NotNewestCopyError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(NOT_NEWEST_MESSAGE)
        return _checklist(student, staff)


class DocumentLinkView(APIView):
    """A short-lived link to open or save one file of a Student's Document (GET; spec 70)."""

    permission_classes = (IsAuthenticated, CanSeeStudents)

    def get(self, request: Request, student_id: int, document_id: int, file_id: int) -> Response:
        """Return the link and how many seconds it works for."""
        student = _student(student_id)
        query = DocumentLinkQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            link = document_link(
                student,
                document_id,
                file_id,
                as_attachment=query.as_attachment(),
                by=get_request_staff(request),
            )
        except DocumentFile.DoesNotExist as error:
            raise NotFound from error
        return Response({"url": link, "expires_in": SIGNED_LINK_SECONDS})


class CanSeePaperwork(BasePermission):
    """The Documents page is for counsellors and Admins (spec 64a)."""

    message = PAPERWORK_ROLES_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the policy lets this Staff member see everyone's paperwork."""
        user = request.user
        return isinstance(user, Staff) and can_act_on_student(user, StudentAction.SEE_PAPERWORK)


class PaperworkView(APIView):
    """The Documents page: Students by how much their paperwork needs looking at (GET)."""

    permission_classes = (IsAuthenticated, CanSeePaperwork)

    def get(self, request: Request) -> Response:
        """Return one page of Students with their counts, and the totals of the whole list."""
        query = PaperworkQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        students = Student.objects.all()
        if query.only_mine():
            students = students.filter(assigned_counsellor=get_request_staff(request))
        if query.only_waiting():
            students = with_waiting_documents(students)
        found = paperwork(students)
        try:
            page = Paginator(found, PAPERWORK_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": PAPERWORK_PER_PAGE,
                "totals": paperwork_totals(found),
                "results": [paperwork_answer(one) for one in page],
            }
        )


class PickableTypesView(APIView):
    """Every Document Type for a document picker (GET), and adding a name not on it (POST).

    For those who choose documents: counsellors and Admins (spec 63c). A type added here is
    asked for only when needed.
    """

    permission_classes = (IsAuthenticated, CanSeePaperwork)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return every Document Type, oldest first."""
        return Response({"results": [document_type_answer(t) for t in document_types()]})

    def post(self, request: Request) -> Response:
        """Add the name as a type asked for only when needed, and return it."""
        serializer = PickedTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _added_type(request, serializer.to_new_type(), configured=False)


def _types_sent(request: Request) -> DocumentTypeListSerializer:
    serializer = DocumentTypeListSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer


class CountryListsView(APIView):
    """Every Country's document list (GET; Configuration, Admin only, spec 121)."""

    permission_classes = (IsAuthenticated, IsAdmin)

    def get(self, request: Request) -> Response:
        """Return each Country's list (empty ones too), in the order Countries are offered."""
        return Response({"results": [country_list_answer(one) for one in country_lists()]})


class CountryListView(APIView):
    """Set one Country's document list (PUT; Configuration, Admin only, spec 121)."""

    permission_classes = (IsAuthenticated, IsAdmin)
    parser_classes = (JSONParser,)

    def put(self, request: Request, country: str) -> Response:
        """Replace the Country's list and return it as stored."""
        listed = _listed_country(country)
        serializer = CountryListSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stored = set_country_requirements(
            listed, serializer.listed(), by=get_request_staff(request)
        )
        return Response(country_list_answer(stored))


def _listed_country(country: str) -> Country:
    """The Country with a document list, by its value; 404 for any other."""
    listed = next((one for one in LISTED_COUNTRIES if one.value == country), None)
    if listed is None:
        raise NotFound
    return listed


def _taken_name_message(existing: DocumentType | None, country: Country) -> str:
    """Why a new document's name is refused: where the type with that name already is."""
    if existing is None:  # pragma: no cover  # added by someone else meanwhile
        return DUPLICATE_TYPE_MESSAGE
    if existing.needed_by_everyone:
        return f"{existing.name} already exists and is Always needed."
    if is_on_country_list(country, existing):
        return f"{existing.name} is already on {country.label}'s list."
    return f'{existing.name} already exists. Pick it from "Add an existing document".'


class NewCountryDocumentView(APIView):
    """Make a new document from a Country's list (POST; Configuration, Admin only, spec 121a).

    The Document Type and its place on the list are created together, or not at all.
    """

    permission_classes = (IsAuthenticated, IsAdmin)
    parser_classes = (JSONParser,)

    def post(self, request: Request, country: str) -> Response:
        """Create the type, put it on the list, and return both as stored."""
        listed = _listed_country(country)
        serializer = NewCountryDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            added, stored = add_new_type_to_country_list(
                listed,
                serializer.name_value(),
                serializer.group_value(),
                by=get_request_staff(request),
            )
        except DuplicateDocumentTypeError as error:
            # expected refusal, answered with its status; the request line logs it
            message = _taken_name_message(error.existing, listed)
            return Response({"name": [message]}, status=status.HTTP_400_BAD_REQUEST)
        except TooManyDocumentTypesError:
            # expected refusal, answered with its status; the request line logs it
            return _refused(TOO_MANY_TYPES_MESSAGE)
        answer = {
            "document_type": configured_type_answer(added),
            "list": country_list_answer(stored),
        }
        return Response(answer, status=status.HTTP_201_CREATED)


class CourseDocumentsView(APIView):
    """Set what one Course also needs (PUT; Admin only, spec 63a)."""

    permission_classes = (IsAuthenticated, CanReadCatalogue, CanChangeCatalogue)
    parser_classes = (JSONParser,)

    def put(self, request: Request, course_id: int) -> Response:
        """Replace its list and return the Course as stored."""
        try:
            course = course_by_id(course_id)
        except Course.DoesNotExist as error:
            raise NotFound from error
        types = _types_sent(request).types()
        set_course_requirements(course, types, by=get_request_staff(request))
        return Response(course_answer(course_by_id(course_id)))


class StudentAskedView(APIView):
    """Set the documents asked for one Student alone (PUT; spec 63b)."""

    permission_classes = (IsAuthenticated, CanSeeStudents)
    parser_classes = (JSONParser,)

    def put(self, request: Request, student_id: int) -> Response:
        """Replace the list and return the checklist as it is now."""
        student = _changeable_student(request, student_id)
        staff = get_request_staff(request)
        types = _types_sent(request).types()
        try:
            set_student_requirements(student, types, by=staff)
        except AlreadyUploadedError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"document_types": [_already_uploaded_message(error.name)]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _checklist(student, staff)


class StudentAskedTypeView(APIView):
    """Take one document asked for one Student alone off their checklist (DELETE; "Not needed")."""

    permission_classes = (IsAuthenticated, CanSeeStudents)

    def delete(self, request: Request, student_id: int, type_id: int) -> Response:
        """Take it off and return the checklist as it is now."""
        student = _changeable_student(request, student_id)
        staff = get_request_staff(request)
        try:
            drop_student_requirement(student, type_id, by=staff)
        except NotAskedError as error:
            raise NotFound from error
        except AlreadyUploadedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(_already_uploaded_message(error.name))
        return _checklist(student, staff)


# Why the Bin refuses, by the service's refusal.
_BIN_REFUSALS: dict[type[Exception], str] = {
    AlreadyInBinError: ALREADY_IN_BIN_MESSAGE,
    NotInBinError: NOT_IN_BIN_MESSAGE,
    OnlyFileError: ONLY_FILE_MESSAGE,
}


class IsBinAdmin(BasePermission):
    """Only an Admin (or a Platform Admin inside) sees the Bin, restores and deletes for good."""

    message = ONLY_ADMIN_MESSAGE

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Return whether the request comes from an active Admin."""
        user = request.user
        return (
            isinstance(user, Staff)
            and user.role == Role.ADMIN
            and can_act_on_student(user, StudentAction.SEE)
        )


def _target(document_id: int, file_id: int | None) -> BinTarget:
    return BinTarget(document_id=document_id, file_id=file_id)


def _nothing_sent(request: Request) -> None:
    NothingSerializer(data=request.data).is_valid(raise_exception=True)


class _BinAction(APIView):
    """One action on a Document, or on one of its files, answered with the checklist."""

    permission_classes: tuple[type[BasePermission], ...] = (IsAuthenticated, CanSeeStudents)
    parser_classes = (JSONParser,)

    def _act(self, request: Request, student: Student, action: Callable[[], None]) -> Response:
        try:
            action()
        except (Document.DoesNotExist, DocumentFile.DoesNotExist) as error:
            raise NotFound from error
        except (AlreadyInBinError, NotInBinError, OnlyFileError) as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(_BIN_REFUSALS[type(error)])
        except NotWhoPutItInError as error:
            raise PermissionDenied(ONLY_WHO_PUT_IT_MESSAGE) from error
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return Response(
                {"detail": NOT_DELETED_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return _checklist(student, get_request_staff(request))


class PutInBinView(_BinAction):
    """Put a Document, or one of its files, in the Bin with a reason (POST; not Admins)."""

    def post(
        self, request: Request, student_id: int, document_id: int, file_id: int | None = None
    ) -> Response:
        """Put it in the Bin and return the checklist as it is now."""
        student = _changeable_student(request, student_id)
        staff = get_request_staff(request)
        if staff.role == Role.ADMIN and not is_platform_visitor(staff):
            raise PermissionDenied(ADMINS_DELETE_MESSAGE)
        serializer = BinReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = _target(document_id, file_id)
        reason = serializer.reason_value()
        return self._act(request, student, lambda: put_in_bin(student, target, reason, staff))


class TakeOutOfBinView(_BinAction):
    """Take something back out of the Bin (POST; only whoever put it there)."""

    def post(
        self, request: Request, student_id: int, document_id: int, file_id: int | None = None
    ) -> Response:
        """Take it out and return the checklist as it is now."""
        student = _student(student_id)
        _nothing_sent(request)
        staff = get_request_staff(request)
        target = _target(document_id, file_id)
        return self._act(request, student, lambda: take_out(student, target, staff))


class RestoreFromBinView(_BinAction):
    """Restore something from the Bin (POST; Admins)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)

    def post(
        self, request: Request, student_id: int, document_id: int, file_id: int | None = None
    ) -> Response:
        """Restore it and return the checklist as it is now."""
        student = _student(student_id)
        _nothing_sent(request)
        staff = get_request_staff(request)
        target = _target(document_id, file_id)
        return self._act(request, student, lambda: restore(student, target, staff))


class DeleteDocumentView(_BinAction):
    """Delete a Document, or one of its files, for good (DELETE; Admins).

    From the Bin, or an Admin's own delete, which skips it. A Platform Admin inside puts things
    in the Bin like Staff do, and deletes for good only from it.
    """

    permission_classes = (IsAuthenticated, IsBinAdmin)

    def delete(
        self, request: Request, student_id: int, document_id: int, file_id: int | None = None
    ) -> Response:
        """Delete it with its stored files and return the checklist as it is now."""
        student = _student(student_id)
        staff = get_request_staff(request)
        target = _target(document_id, file_id)
        if is_platform_visitor(staff) and not _is_in_bin(student, target):
            raise PermissionDenied(PUT_IT_IN_THE_BIN_MESSAGE)
        return self._act(request, student, lambda: delete_for_good(student, target, staff))


def _is_in_bin(student: Student, target: BinTarget) -> bool:
    """Whether the Document or file meant is in the Bin itself (unknown ones are not)."""
    if target.file_id is None:
        return Document.objects.filter(
            student=student, pk=target.document_id, binned_at__isnull=False
        ).exists()
    return DocumentFile.objects.filter(
        document__student=student,
        document_id=target.document_id,
        pk=target.file_id,
        binned_at__isnull=False,
    ).exists()


class BinView(APIView):
    """The Bin tab: everything waiting in the Bin, newest first (GET; Admins)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)

    def get(self, request: Request) -> Response:
        """Return one page of the Bin."""
        query = BinQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            page = Paginator(_everything_in_bin(), BIN_PER_PAGE).page(query.page_number())
        except EmptyPage as error:
            raise NotFound from error
        return Response(
            {
                "count": page.paginator.count,
                "page": page.number,
                "page_size": BIN_PER_PAGE,
                "results": [_bin_row(item) for item in page],
            }
        )


def _everything_in_bin() -> list[BinItem | CourseFile]:
    """Documents and their files, and Course Files (spec 56), newest first, each with its kind."""
    items: list[BinItem | CourseFile] = [*bin_items(), *binned_course_files()]
    return sorted(items, key=_binned_at, reverse=True)


def _binned_at(item: BinItem | CourseFile) -> tuple[datetime, int, int]:
    if isinstance(item, BinItem):
        return item.binned_at, 0, item.document.pk
    at = item.binned_at
    assert at is not None  # noqa: S101  # only Course Files in the Bin are listed
    return at, 1, item.pk


def _bin_row(item: BinItem | CourseFile) -> dict[str, object]:
    return bin_item_answer(item) if isinstance(item, BinItem) else binned_course_file_answer(item)


class BinFileLinkView(APIView):
    """A short-lived link to a file in the Bin, for an Admin on the Bin tab (GET)."""

    permission_classes = (IsAuthenticated, IsBinAdmin)

    def get(self, request: Request, file_id: int) -> Response:
        """Return the link and how many seconds it works for."""
        query = DocumentLinkQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            file = file_in_bin(file_id)
        except DocumentFile.DoesNotExist as error:
            raise NotFound from error
        link = signed_file_link(
            file, as_attachment=query.as_attachment(), by=get_request_staff(request)
        )
        return Response({"url": link, "expires_in": SIGNED_LINK_SECONDS})
