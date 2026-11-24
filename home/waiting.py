"""What's waiting on a Counsellor or an Admin, for their Home (issues 52, 75; spec 22a1).

Call inside the Consultancy's schema. Who may ask is the view's job. The counts take a fixed
number of queries, however much is waiting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from access.policy import why_cannot_manage_work_from_home
from applications.appointments import Booked
from applications.models import Visa
from applications.statuses import VisaStatus
from clock_in.work_from_home import waiting as work_from_home_waiting
from documents.bin import count_in_bin
from documents.services import waiting_count
from staff.actors import is_platform_visitor
from staff.models import Role, Staff
from students.models import Student
from transfer_requests.services import waiting_for

# "The next 7 days": today and the six days after it, as the Visas list's filter counts them.
APPOINTMENT_DAYS = 7
# How many of the coming appointments the Home lists; the count covers them all.
APPOINTMENTS_SHOWN = 5


@dataclass(frozen=True)
class UpcomingAppointment:
    """One Biometrics, Medical or Interview coming up, and the Visa it is on."""

    visa: Visa
    booked: Booked


@dataclass(frozen=True)
class Waiting:
    """What waits on one person: counts, and the first few Visa appointments coming up."""

    documents: int
    transfer_requests: int
    # How many items wait in the Bin: for Admins only, 0 for everyone else (issue 79).
    documents_in_bin: int
    appointment_count: int
    appointments: list[UpcomingAppointment]
    # Work From Home requests waiting for an Admin, and whose is oldest (issue 87).
    work_from_home_requests: int = 0
    work_from_home_first_staff_id: UUID | None = None


def waiting_on(staff: Staff, *, clock_in_on: bool = False) -> Waiting:
    """What waits on `staff`.

    An Admin (or a Platform Admin inside) sees everyone's Documents and, with Clock-in on, the
    Work From Home requests; a Counsellor their own Students' Documents and Visa appointments in
    the next 7 days.
    """
    everyone = staff.role == Role.ADMIN or is_platform_visitor(staff)
    students = Student.objects.all()
    if not everyone:
        students = students.filter(assigned_counsellor_id=staff.pk)
    appointments = (
        _appointments_soon(staff, timezone.localdate())
        if staff.role == Role.COUNSELLOR and not is_platform_visitor(staff)
        else []
    )
    requests: list[UUID] = []
    if clock_in_on and why_cannot_manage_work_from_home(staff) is None:
        requests = list(work_from_home_waiting().values_list("staff__public_id", flat=True))
    return Waiting(
        work_from_home_requests=len(requests),
        work_from_home_first_staff_id=requests[0] if requests else None,
        documents=waiting_count(students),
        transfer_requests=waiting_for(staff).count(),
        documents_in_bin=count_in_bin() if staff.role == Role.ADMIN else 0,
        appointment_count=len(appointments),
        appointments=appointments[:APPOINTMENTS_SHOWN],
    )


def _appointments_soon(staff: Staff, today: date) -> list[UpcomingAppointment]:
    """The counsellor's Students' appointments from the start of today, for seven days.

    Only on Visas waiting for an answer; soonest first. One query.
    """
    start, end = _start_of(today), _start_of(today + timedelta(days=APPOINTMENT_DAYS))
    in_window = Q()
    for field in ("biometrics_at", "medical_at", "interview_at"):
        in_window |= Q(**{f"{field}__gte": start, f"{field}__lt": end})
    visas: QuerySet[Visa] = (
        Visa.objects.filter(
            in_window, student__assigned_counsellor_id=staff.pk, status=VisaStatus.APPLIED
        )
        .select_related("student")
        .order_by("pk")
    )
    found = [
        UpcomingAppointment(visa, booked)
        for visa in visas
        for booked in visa.dates.booked()
        if start <= booked.at < end
    ]
    return sorted(found, key=lambda one: (one.booked.at, one.visa.pk))


def _start_of(day: date) -> datetime:
    """Midnight at the start of `day` in the Consultancy's time zone."""
    return timezone.make_aware(datetime.combine(day, time.min))
