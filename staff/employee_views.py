"""REST endpoints for the Employees page (spec 18-21), for Admins only.

Plus the list of who can be picked as an Assigned Counsellor, for everyone but Teachers.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.models import TokenKind
from codes.exceptions import CodeLimitReachedError
from staff.context import get_request_staff
from staff.employees import (
    cancel_invite,
    change_role,
    deactivate_employee,
    invite_employee,
    list_counsellors,
    list_employees,
    resend_invite,
)
from staff.exceptions import EmailTakenError, EmployeeChangeRefusedError, RoleNotAvailableError
from staff.models import Staff
from staff.permissions import IsEmployeesAdmin, IsNotTeacher
from staff.serializers import (
    EMAIL_TAKEN_MESSAGE,
    TEACHER_NEEDS_CLASSES_MESSAGE,
    InviteSerializer,
    RoleChangeSerializer,
)
from staff.types import Office
from students.journey import students_still_assigned_to
from tenancy.context import get_request_consultancy
from two_factor.services import is_set_up, set_up_ids

JOIN_PATH = "/join"
CODE_LIMIT_MESSAGE = "Too many codes were sent in the last hour. Try again later."


def employee_answer(staff: Staff, set_up: bool | None = None) -> dict[str, object]:
    """One employee as the Employees page shows them. Never anything about their password.

    Their two-step login is shown as on or off and set up or not (issue 62), never the secret;
    a list passes `set_up` it looked up for everyone at once.
    """
    return {
        "id": staff.pk,
        "name": staff.name,
        "email": staff.email,
        "mobile": staff.mobile,
        "job_title": staff.job_title,
        "role": staff.role,
        "joined_on": staff.joined_on.isoformat(),
        "status": staff.status,
        "two_factor_required": staff.two_factor_required,
        "two_factor_set_up": is_set_up(staff) if set_up is None else set_up,
    }


def office_of(request: Request) -> Office:
    """The office and its sign-up page, at the address the request came in on."""
    consultancy = get_request_consultancy(request)
    return Office(consultancy=consultancy, join_link=request.build_absolute_uri(JOIN_PATH))


def _refused(error: EmployeeChangeRefusedError) -> Response:
    return Response({"detail": error.message}, status=status.HTTP_400_BAD_REQUEST)


def _role_refused() -> Response:
    return Response({"role": [TEACHER_NEEDS_CLASSES_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)


class _AdminView(APIView):
    permission_classes = (IsAuthenticated, IsEmployeesAdmin)


class EmployeeListView(_AdminView):
    """List the office's Employees, or add (invite) one."""

    def get(self, request: Request) -> Response:
        """Return every Employee, invited and departed included."""
        employees = list(list_employees())
        set_up = set_up_ids(TokenKind.STAFF, [staff.pk for staff in employees])
        return Response([employee_answer(staff, staff.pk in set_up) for staff in employees])

    def post(self, request: Request) -> Response:
        """Add someone as invited and email them a Code to join with."""
        serializer = InviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            staff = invite_employee(
                serializer.to_new_staff(),
                get_request_consultancy(request),
                office_of(request),
                by=get_request_staff(request),
            )
        except RoleNotAvailableError:
            # expected refusal, answered with its status; the request line logs it
            return _role_refused()
        except EmailTakenError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"email": [EMAIL_TAKEN_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(employee_answer(staff), status=status.HTTP_201_CREATED)


class EmployeeView(_AdminView):
    """Change an Employee's Role."""

    def patch(self, request: Request, staff_id: int) -> Response:
        """Give them the Role sent, and return them as stored."""
        staff = get_object_or_404(Staff, pk=staff_id)
        serializer = RoleChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_role(
                staff,
                serializer.validated_data["role"],
                get_request_consultancy(request),
                by=get_request_staff(request),
            )
        except RoleNotAvailableError:
            # expected refusal, answered with its status; the request line logs it
            return _role_refused()
        except EmployeeChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error)
        return Response(employee_answer(staff))


class EmployeeDeactivateView(_AdminView):
    """Deactivate someone who has left."""

    def post(self, request: Request, staff_id: int) -> Response:
        """End their logins, stop them logging in, and return them as stored."""
        staff = get_object_or_404(Staff, pk=staff_id)
        try:
            deactivate_employee(staff, by=get_request_staff(request))
        except EmployeeChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error)
        return Response(employee_answer(staff))


class EmployeeInviteView(_AdminView):
    """Send the code again (POST) or cancel the invite (DELETE) of someone not joined yet."""

    def post(self, request: Request, staff_id: int) -> Response:
        """Email them a new Code; the old one stops working."""
        staff = get_object_or_404(Staff, pk=staff_id)
        try:
            resend_invite(staff, office_of(request), by=get_request_staff(request))
        except EmployeeChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error)
        except CodeLimitReachedError:
            # expected refusal, answered with its status; the request line logs it
            return Response(
                {"detail": CODE_LIMIT_MESSAGE}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        return Response(employee_answer(staff), status=status.HTTP_202_ACCEPTED)

    def delete(self, request: Request, staff_id: int) -> Response:
        """Remove them; their Code no longer works."""
        staff = get_object_or_404(Staff, pk=staff_id)
        try:
            cancel_invite(staff, by=get_request_staff(request))
        except EmployeeChangeRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return _refused(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeAssignedStudentsView(_AdminView):
    """The Students someone still counsels, to Transfer before deactivating them (spec 21)."""

    def get(self, request: Request, staff_id: int) -> Response:
        """Return each one's id, name and Stage, by name."""
        staff = get_object_or_404(Staff, pk=staff_id)
        return Response(
            [
                {"id": student.public_id, "name": student.name, "stage": student.stage}
                for student in students_still_assigned_to(staff)
            ]
        )


class CounsellorChoicesView(APIView):
    """Who can be picked as an Assigned Counsellor: active Counsellors and Admins (spec 22b)."""

    permission_classes = (IsAuthenticated, IsNotTeacher)

    def get(self, request: Request) -> Response:
        """Return each one's id, name and Role."""
        return Response([{"id": s.pk, "name": s.name, "role": s.role} for s in list_counsellors()])
