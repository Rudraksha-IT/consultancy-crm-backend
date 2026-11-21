"""REST endpoints for a Student's Applications and Visas (spec 52-62)."""

from __future__ import annotations

from uuid import UUID

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from access.policy import StudentAction, why_cannot
from applications.exceptions import (
    AlreadyAppliedError,
    AlreadyChosenError,
    ChoiceLockedError,
    NoOfferError,
    NotChosenOfferError,
    NotWaitingForOfferError,
    OfferNeedsLetterError,
    OfferStaysError,
    SameStatusError,
    TooManyApplicationsError,
    TooManyVisasError,
    VisaGrantedError,
    VisaNotWaitingError,
    VisaWaitingError,
)
from applications.models import Application, Visa
from applications.serializers import (
    CHOOSE_APPLICATION_MESSAGE,
    ChosenOfferSerializer,
    NewApplicationSerializer,
    NewVisaSerializer,
    StatusChangeSerializer,
    VisaDatesSerializer,
    VisaStatusChangeSerializer,
)
from applications.services import (
    MAX_APPLICATIONS_PER_STUDENT,
    MAX_VISAS_PER_STUDENT,
    add_application,
    change_application_status,
    change_visa_dates,
    change_visa_status,
    choose_offer,
    start_visa,
)
from config.public_ids import pk_for
from documents.exceptions import (
    DocumentStorageError,
    TooManyDocumentsError,
    VisaAnsweredError,
    VisaResultNeededError,
)
from documents.offer_letters import add_offer_letter
from documents.serializers import CHOOSE_RESULT_MESSAGE, OfferLetterSerializer, VisaLetterSerializer
from documents.services import MAX_DOCUMENTS_PER_STUDENT
from documents.visa_letters import add_visa_letter
from staff.context import get_request_staff
from students.answers import student_profile
from students.models import Student
from students.permissions import CanSeeStudents
from students.profile import profile_of

TOO_MANY_MESSAGE = f"A Student can have at most {MAX_APPLICATIONS_PER_STUDENT} applications."
ALREADY_APPLIED_MESSAGE = "This Student already has an application for this course and intake."
SAME_STATUS_MESSAGE = "The application already has this status."
OFFER_NEEDS_LETTER_MESSAGE = "Offer is set by adding an offer letter."
OFFER_STAYS_MESSAGE = "An application with an offer stays at Offer."
TOO_MANY_VISAS_MESSAGE = f"A Student can have at most {MAX_VISAS_PER_STUDENT} visas."
VISA_WAITING_MESSAGE = "This Student already has a visa waiting for an answer."
VISA_GRANTED_MESSAGE = "This application already has a granted visa."
SAME_VISA_STATUS_MESSAGE = "The visa already has this status."
VISA_NOT_WAITING_MESSAGE = "Only a visa waiting for an answer has dates to change."
WAITING_FOR_OFFER_MESSAGE = "Choose an application waiting for an offer."
TOO_MANY_DOCUMENTS_MESSAGE = f"A Student can have at most {MAX_DOCUMENTS_PER_STUDENT} documents."
NOT_STORED_MESSAGE = "We couldn't store the file. Try again."
ALREADY_CHOSEN_MESSAGE = "This offer is already chosen."
CHOICE_LOCKED_MESSAGE = (
    "The visa for the chosen offer is waiting for an answer, so the choice can't change now."
)
NOT_CHOSEN_MESSAGE = "A visa can only be applied for on the chosen offer."
VISA_NOT_CHOSEN_MESSAGE = "This visa isn't for the chosen offer."
VISA_ANSWERED_MESSAGE = "This visa is already {status}. To change it, set its status by hand."


def _workable_student(request: Request, student_id: UUID) -> Student:
    """The Student, if the Staff member may work on their Applications and Visas; else 404/403."""
    try:
        student = profile_of(student_id)
    except Student.DoesNotExist as error:
        raise NotFound from error
    reason = why_cannot(get_request_staff(request), StudentAction.CHANGE_APPLICATIONS, student)
    if reason is not None:
        raise PermissionDenied(reason)
    return student


def _own_application(student: Student, application_id: UUID) -> int:
    """The database number of one of the Student's Applications; 404 for any other."""
    found = pk_for(student.applications.all(), application_id)
    if not found:
        raise NotFound
    return found


def _own_visa(student: Student, visa_id: UUID) -> int:
    """The database number of one of the Student's Visas; 404 for any other."""
    found = pk_for(student.visas.all(), visa_id)
    if not found:
        raise NotFound
    return found


def _profile(request: Request, student_id: UUID, code: int = status.HTTP_200_OK) -> Response:
    student = profile_of(student_id)
    return Response(student_profile(student, viewer=get_request_staff(request)), status=code)


def _refused(field: str, message: str) -> Response:
    return Response({field: [message]}, status=status.HTTP_400_BAD_REQUEST)


class _ApplicationView(APIView):
    permission_classes = (IsAuthenticated, CanSeeStudents)


class ApplicationListView(_ApplicationView):
    """Add an Application to a Student (POST; spec 52)."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Add it as Applied and return the Student's profile as stored."""
        student = _workable_student(request, student_id)
        serializer = NewApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            add_application(student, serializer.to_new_application(), by=get_request_staff(request))
        except TooManyApplicationsError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": TOO_MANY_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        except AlreadyAppliedError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("intake", ALREADY_APPLIED_MESSAGE)
        return _profile(request, student_id, status.HTTP_201_CREATED)


class ApplicationView(_ApplicationView):
    """Change one Application's Status (PATCH; spec 53-56)."""

    def patch(self, request: Request, student_id: UUID, application_id: UUID) -> Response:
        """Set the Status, let the Stage follow, and return the Student's profile as stored."""
        student = _workable_student(request, student_id)
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_application_status(
                student,
                _own_application(student, application_id),
                serializer.status_value(),
                by=get_request_staff(request),
            )
        except Application.DoesNotExist as error:
            raise NotFound from error
        except OfferNeedsLetterError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("status", OFFER_NEEDS_LETTER_MESSAGE)
        except OfferStaysError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("status", OFFER_STAYS_MESSAGE)
        except SameStatusError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("status", SAME_STATUS_MESSAGE)
        return _profile(request, student_id)


class OfferLetterListView(_ApplicationView):
    """Add an Offer Letter to one of a Student's Applications (POST, a form; spec 114)."""

    # A form with one file; nothing else is read.
    parser_classes = (MultiPartParser,)

    def post(self, request: Request, student_id: UUID) -> Response:
        """Store the letter, make the Application Offer, and return the Student's profile."""
        student = _workable_student(request, student_id)
        serializer = OfferLetterSerializer(data=request.data, context={"student": student})
        serializer.is_valid(raise_exception=True)
        try:
            add_offer_letter(
                student, serializer.to_new_offer_letter(), by=get_request_staff(request)
            )
        except NotWaitingForOfferError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("application", WAITING_FOR_OFFER_MESSAGE)
        except TooManyDocumentsError:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": TOO_MANY_DOCUMENTS_MESSAGE}, status=status.HTTP_400_BAD_REQUEST
            )
        except DocumentStorageError:
            # silent: the storage call already logged the failure at ERROR
            return Response(
                {"detail": NOT_STORED_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return _profile(request, student_id, status.HTTP_201_CREATED)


class ChosenOfferView(_ApplicationView):
    """Choose the offer the Student goes ahead with, replacing any other (PUT; spec 115)."""

    parser_classes = (JSONParser,)

    def put(self, request: Request, student_id: UUID) -> Response:
        """Set the Chosen Offer and return the Student's profile as stored."""
        student = _workable_student(request, student_id)
        serializer = ChosenOfferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            choose_offer(
                student,
                pk_for(student.applications.all(), serializer.application_id()),
                by=get_request_staff(request),
            )
        except NoOfferError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("application", CHOOSE_APPLICATION_MESSAGE)
        except AlreadyChosenError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("application", ALREADY_CHOSEN_MESSAGE)
        except ChoiceLockedError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("application", CHOICE_LOCKED_MESSAGE)
        return _profile(request, student_id)


class VisaListView(_ApplicationView):
    """Add a Visa, once sent to the embassy, for one of a Student's Applications (POST; spec 58)."""

    def post(self, request: Request, student_id: UUID) -> Response:
        """Add it as Applied, let the Stage follow, and return the Student's profile as stored."""
        student = _workable_student(request, student_id)
        serializer = NewVisaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            start_visa(
                student,
                pk_for(student.applications.all(), serializer.application_id()),
                by=get_request_staff(request),
            )
        except NotChosenOfferError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("application", NOT_CHOSEN_MESSAGE)
        except VisaWaitingError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("application", VISA_WAITING_MESSAGE)
        except VisaGrantedError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("application", VISA_GRANTED_MESSAGE)
        except TooManyVisasError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": TOO_MANY_VISAS_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        return _profile(request, student_id, status.HTTP_201_CREATED)


class VisaView(_ApplicationView):
    """Change one Visa's Status (PATCH; spec 59-61)."""

    def patch(self, request: Request, student_id: UUID, visa_id: UUID) -> Response:
        """Set the Status, let the Stage follow, and return the Student's profile as stored."""
        student = _workable_student(request, student_id)
        serializer = VisaStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_visa_status(
                student,
                _own_visa(student, visa_id),
                serializer.status_value(),
                by=get_request_staff(request),
            )
        except Visa.DoesNotExist as error:
            raise NotFound from error
        except SameStatusError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("status", SAME_VISA_STATUS_MESSAGE)
        except VisaWaitingError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("status", VISA_WAITING_MESSAGE)
        except NotChosenOfferError:
            # expected refusal, answered with its status; the request line logs it
            return _refused("status", VISA_NOT_CHOSEN_MESSAGE)
        return _profile(request, student_id)


class VisaDatesView(_ApplicationView):
    """Book, move or clear one Visa's Biometrics, Medical or Interview (PATCH; spec 62a)."""

    def patch(self, request: Request, student_id: UUID, visa_id: UUID) -> Response:
        """Change the dates sent (null clears one) and return the Student's profile as stored."""
        student = _workable_student(request, student_id)
        serializer = VisaDatesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_visa_dates(
                student,
                _own_visa(student, visa_id),
                serializer.change(),
                by=get_request_staff(request),
            )
        except Visa.DoesNotExist as error:
            raise NotFound from error
        except VisaNotWaitingError:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": VISA_NOT_WAITING_MESSAGE}, status=status.HTTP_400_BAD_REQUEST
            )
        return _profile(request, student_id)


class VisaLetterView(_ApplicationView):
    """Upload a Visa's letter, setting Granted or Refused on a Visa at Applied (POST; spec 116)."""

    # A form with one file; nothing else is read.
    parser_classes = (MultiPartParser,)

    def post(self, request: Request, student_id: UUID, visa_id: UUID) -> Response:
        """Store the letter (and the result), and return the Student's profile as stored."""
        student = _workable_student(request, student_id)
        serializer = VisaLetterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            add_visa_letter(
                student,
                _own_visa(student, visa_id),
                serializer.to_new_visa_letter(),
                by=get_request_staff(request),
            )
        except Visa.DoesNotExist as error:
            raise NotFound from error
        except (
            VisaResultNeededError,
            VisaAnsweredError,
            TooManyDocumentsError,
            DocumentStorageError,
        ) as error:
            return _visa_letter_refused(error)
        return _profile(request, student_id, status.HTTP_201_CREATED)


def _visa_letter_refused(error: Exception) -> Response:
    """The short, safe answer for a visa letter that wasn't stored."""
    if isinstance(error, VisaResultNeededError):
        # expected refusal, answered with its status; the request line logs it
        return _refused("result", CHOOSE_RESULT_MESSAGE)
    if isinstance(error, VisaAnsweredError):
        # expected refusal, answered with its status; the request line logs it
        return _refused("result", VISA_ANSWERED_MESSAGE.format(status=error.current))
    if isinstance(error, TooManyDocumentsError):
        # expected refusal, answered with its status; the request line logs it
        return Response({"detail": TOO_MANY_DOCUMENTS_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
    # silent: the storage call already logged the failure at ERROR
    return Response({"detail": NOT_STORED_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
