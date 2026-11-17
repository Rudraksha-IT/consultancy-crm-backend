"""The one permission policy: can this Staff member do this action on this Student? (spec 33).

Every Role rule about Students lives here, so views and services ask instead of deciding.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import StrEnum

from staff.models import PlatformVisitor, Role, Staff, StaffStatus
from students.models import Stage, Student

logger = logging.getLogger(__name__)

# Roles that work with Students; Teachers only see their own Batches (spec 22).
STUDENT_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
# Roles that assign a Counsellor to a Student who has none (spec 28).
ASSIGNING_ROLES = (Role.ADMIN, Role.FRONT_DESK)
# Roles that look after paperwork across Students: the Documents page (spec 64a).
PAPERWORK_ROLES = (Role.ADMIN, Role.COUNSELLOR)
# Roles that take Payments from any Student (spec 71); only Admins see them afterwards (spec 73).
PAYMENT_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
# Roles that see every Payment, and change or delete one (spec 74-75).
PAYMENT_MANAGING_ROLES = (Role.ADMIN,)
# Roles that see, add, change and delete the office's Expenses (spec 76c).
EXPENSE_ROLES = (Role.ADMIN,)
# Roles that see, add and change Commission from Universities (spec 76d).
COMMISSION_ROLES = (Role.ADMIN,)
# Roles that export lists as CSV files and see the Export Records (spec 31b, 31c).
EXPORT_ROLES = (Role.ADMIN,)
# Roles that add and remove the office's Office Networks for Clock-in (spec 105).
OFFICE_NETWORK_ROLES = (Role.ADMIN,)
# Roles that see everyone's working hours, correct a day and set the Working Day (spec 105a-108).
WORKING_HOURS_ROLES = (Role.ADMIN,)
# Who clocks in from any network, and so never needs Work From Home dates (issue 87).
CLOCK_ANYWHERE_ROLES = (Role.ADMIN,)
# Roles that see test-prep classes; Teachers only those they teach (spec 22, 83).
BATCH_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.TEACHER)
# Roles that create and edit classes and fill them, and see everyone enrolled (spec 83-84, 86a).
BATCH_MANAGING_ROLES = (Role.ADMIN, Role.COUNSELLOR)
# Roles that see Students' messages and send SMS or email to one (spec 89, 91); not Teachers.
MESSAGE_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
# Roles that log a Student's office Visits and see them (spec 23a-23c); not Teachers.
VISIT_ROLES = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)
# Roles that ask to take over a Student who isn't theirs (spec 44a): counsellors, and Admins.
TRANSFER_ASKING_ROLES = (Role.ADMIN, Role.COUNSELLOR)
# Roles that switch Staff members' two-step login on or off and reset it (issue 62).
TWO_FACTOR_MANAGING_ROLES = (Role.ADMIN,)
# How long whoever recorded a Payment can still open its receipt, e.g. to print it again.
RECEIPT_WINDOW = timedelta(hours=1)

# The short reasons a refused change gives (403).
TEACHERS_MESSAGE = "Teachers can't see Students."
NOT_YOUR_STUDENT_MESSAGE = "Not your Student. Only their counsellor or an Admin can change them."
FRONT_DESK_LEADS_ONLY_MESSAGE = "Front Desk can change only Leads with no counsellor."
COUNSELLOR_OR_ADMIN_MESSAGE = "Only the Student's counsellor or an Admin can do this."
ASSIGN_ROLES_MESSAGE = "Only an Admin or Front Desk can assign a counsellor."
ALREADY_ASSIGNED_MESSAGE = "This Student already has a counsellor. Transfer them instead."
NOT_ASSIGNED_YET_MESSAGE = "This Student has no counsellor yet. Assign one instead."
PAPERWORK_ROLES_MESSAGE = "Only counsellors and Admins can see this."
ONLY_ADMIN_SEES_PAYMENTS_MESSAGE = "Only an Admin can see other Payments."
ONLY_ADMIN_EXPENSES_MESSAGE = "Only an Admin can see or change Expenses."
ONLY_ADMIN_COMMISSION_MESSAGE = "Only an Admin can see or change Commission."
ONLY_ADMIN_EXPORTS_MESSAGE = "Only an Admin can export lists."
ONLY_ADMIN_OFFICE_NETWORKS_MESSAGE = "Only an Admin can manage the office network."
ONLY_ADMIN_WORKING_HOURS_MESSAGE = "Only an Admin can see and correct everyone's working hours."
SOMEONE_ELSES_HOURS_MESSAGE = "Only an Admin can see someone else's working hours."
PLATFORM_CANT_CLOCK_MESSAGE = "A Platform Admin can't clock in or out."
ONLY_ACTIVE_STAFF_CLOCK_MESSAGE = "Only Staff who work here can clock in or out."
ADMIN_NEEDS_NO_WORK_FROM_HOME_MESSAGE = (
    "Admins can clock in from any network, so they never need Work from home dates."
)
ONLY_ADMIN_WORK_FROM_HOME_MESSAGE = "Only an Admin can answer or set Work from home dates."
ONLY_ADMIN_CHANGES_PAYMENTS_MESSAGE = "Only an Admin can change or delete a Payment."
CANT_TICK_TASK_MESSAGE = "Only the person it's for, who gave it, or an Admin can tick it."
BATCH_ROLES_MESSAGE = "Only Admins, counsellors and teachers can see classes."
CHANGE_BATCHES_MESSAGE = "Only an Admin or a counsellor can change classes."
TAKE_ATTENDANCE_MESSAGE = "Only this class's teacher or an Admin can take attendance."
ASK_ROLES_MESSAGE = "Only counsellors and Admins can ask to take a Student."
ALREADY_YOURS_MESSAGE = "This Student is already yours."
PLATFORM_CANT_TAKE_MESSAGE = "A Platform Admin can't take Students."
ANSWER_ASSIGNED_MESSAGE = "Only the Student's counsellor or an Admin can answer this request."
ANSWER_UNASSIGNED_MESSAGE = (
    "Only Front Desk or an Admin can answer for a Student with no counsellor."
)
OWN_REQUEST_MESSAGE = "You can't answer your own request."
ONLY_REQUESTER_CANCELS_MESSAGE = "Only the person who asked can cancel this request."
ONLY_ADMIN_SEES_ALL_REQUESTS_MESSAGE = "Only an Admin can see everyone's transfer requests."
PLATFORM_CANT_READ_INBOX_MESSAGE = (
    "Inbox conversations are private to the two people. Platform Admins can't read them."
)
ONLY_ADMIN_TWO_FACTOR_MESSAGE = "Only an Admin can change someone's two-step login."
OWN_TWO_FACTOR_MESSAGE = "Change your own two-step login in My settings."
ADMIN_REQUIRES_TWO_FACTOR_MESSAGE = (
    "Your Admin requires two-step login for you, so only they can switch it off."
)


class StudentAction(StrEnum):
    """What a Staff member may want to do with Students."""

    SEE = "see"  # List, search and open any Student (spec 29, 32).
    ADD = "add"  # Add a new visitor as a Lead (spec 23).
    CHANGE = "change"  # Change one Student's record (spec 27, 33).
    # Open Edit details and save every field and list at once: their counsellor or an Admin
    # (issue 81). Front Desk's corrections to an open Lead stay with CHANGE.
    EDIT_DETAILS = "edit_details"
    SET_STAGE = "set_stage"  # Move their Stage by hand, or mark them Lost (spec 38-40).
    ASSIGN = "assign"  # Give a Student with no counsellor their first one (spec 28, 44).
    TRANSFER = "transfer"  # Hand a Student to another Counsellor (spec 42, 43).
    # Add Applications and start Visas, and change their Status (spec 52-53, 58-59).
    CHANGE_APPLICATIONS = "change_applications"
    # Upload a Student's Documents (spec 65); checking them comes with issue 17.
    CHANGE_DOCUMENTS = "change_documents"
    # See every Student's paperwork on the Documents page (spec 64a).
    SEE_PAPERWORK = "see_paperwork"
    # See Students' progress as arrow steps on the Counsellor's and Admin's Homes (spec 22a).
    SEE_PROGRESS = "see_progress"
    # Record a Payment from any Student and print its receipt (spec 71-72).
    RECORD_PAYMENT = "record_payment"
    # See every Payment, and change or delete one (spec 74-75).
    MANAGE_PAYMENTS = "manage_payments"
    # See Students' messages, and send an SMS or email to any Student (spec 89, 91).
    MESSAGE = "message"
    # Log any Student's Visit to the office, and see Visits (spec 23a-23c).
    LOG_VISIT = "log_visit"
    # See, add, change and delete one Student's Fees: their counsellor's or an Admin's (spec 76a).
    CHANGE_FEES = "change_fees"
    # See Balances: what each Student has paid and still owes, and their totals (spec 73, 76b).
    SEE_BALANCES = "see_balances"


# Actions about one Student, which need that Student to answer.
_ONE_STUDENT_ACTIONS = frozenset(
    {
        StudentAction.CHANGE,
        StudentAction.EDIT_DETAILS,
        StudentAction.SET_STAGE,
        StudentAction.ASSIGN,
        StudentAction.TRANSFER,
        StudentAction.CHANGE_APPLICATIONS,
        StudentAction.CHANGE_DOCUMENTS,
        StudentAction.CHANGE_FEES,
    }
)
# Actions only some of the Student Roles may do, about no Student in particular.
_ROLE_ACTIONS: dict[StudentAction, tuple[Role, ...]] = {
    StudentAction.SEE_PAPERWORK: PAPERWORK_ROLES,
    StudentAction.SEE_PROGRESS: PAPERWORK_ROLES,
    StudentAction.RECORD_PAYMENT: PAYMENT_ROLES,
    StudentAction.MANAGE_PAYMENTS: PAYMENT_MANAGING_ROLES,
    StudentAction.SEE_BALANCES: PAYMENT_MANAGING_ROLES,
    StudentAction.MESSAGE: MESSAGE_ROLES,
    StudentAction.LOG_VISIT: VISIT_ROLES,
}


def can_act_on_student(staff: Staff, action: StudentAction, student: Student | None = None) -> bool:
    """Return whether `staff` may do `action` (on `student`, which one-Student actions need)."""
    if staff.status != StaffStatus.ACTIVE or staff.role not in STUDENT_ROLES:
        return False
    if action in _ROLE_ACTIONS:
        return staff.role in _ROLE_ACTIONS[action]
    if action not in _ONE_STUDENT_ACTIONS:
        return True
    return student is not None and _why_not(staff, action, student) is None


def why_cannot(staff: Staff, action: StudentAction, student: Student) -> str | None:
    """Return why `staff` may not do `action` on `student`, or None when they may."""
    if staff.role == Role.TEACHER:
        return TEACHERS_MESSAGE
    if staff.status != StaffStatus.ACTIVE or staff.role not in STUDENT_ROLES:
        return NOT_YOUR_STUDENT_MESSAGE
    if action not in _ONE_STUDENT_ACTIONS:
        return None
    reason = _why_not(staff, action, student)
    if reason is not None:
        logger.debug(
            "student action refused: action=%s staff_id=%s student_id=%s",
            action,
            staff.pk,
            student.pk,
        )
    return reason


def why_cannot_change(staff: Staff, student: Student) -> str | None:
    """Return why `staff` may not change `student`'s record, or None when they may."""
    return why_cannot(staff, StudentAction.CHANGE, student)


def why_cannot_see_payment(
    staff: Staff, recorded_by_id: int | None, recorded_at: datetime, now: datetime
) -> str | None:
    """Return why `staff` may not see a Payment, or None when they may (spec 72-73).

    Admins see every Payment. Whoever recorded one sees it for `RECEIPT_WINDOW` afterwards, to
    print its receipt; nobody else does.
    """
    if staff.role == Role.TEACHER:
        return TEACHERS_MESSAGE
    if staff.status != StaffStatus.ACTIVE:
        return ONLY_ADMIN_SEES_PAYMENTS_MESSAGE
    if staff.role == Role.ADMIN:
        return None
    is_own_and_recent = recorded_by_id == staff.pk and now - recorded_at <= RECEIPT_WINDOW
    return None if is_own_and_recent else ONLY_ADMIN_SEES_PAYMENTS_MESSAGE


def why_cannot_mark_task_done(
    staff: Staff, assignee_id: int, created_by_id: int | None
) -> str | None:
    """Return why `staff` may not mark a Task done or reopen it, or None when they may (spec 81).

    The person it's for and whoever gave it may, and so may any Admin.
    """
    if staff.status != StaffStatus.ACTIVE:
        return CANT_TICK_TASK_MESSAGE
    if staff.role == Role.ADMIN or staff.pk in {assignee_id, created_by_id}:
        return None
    return CANT_TICK_TASK_MESSAGE


def why_cannot_link_student(staff: Staff) -> str | None:
    """Return why `staff` may not tie a Task to a Student, or None when they may (spec 77)."""
    return TEACHERS_MESSAGE if staff.role == Role.TEACHER else None


def why_cannot_manage_expenses(staff: Staff) -> str | None:
    """Return why `staff` may not see or change Expenses, or None when they may (spec 76c)."""
    if staff.status == StaffStatus.ACTIVE and staff.role in EXPENSE_ROLES:
        return None
    return ONLY_ADMIN_EXPENSES_MESSAGE


def why_cannot_export(staff: Staff) -> str | None:
    """Return why `staff` may not export lists or see Export Records, or None (spec 31b, 31c)."""
    if staff.status == StaffStatus.ACTIVE and staff.role in EXPORT_ROLES:
        return None
    return ONLY_ADMIN_EXPORTS_MESSAGE


def why_cannot_manage_office_networks(staff: Staff) -> str | None:
    """Return why `staff` may not see or change the Office Networks, or None (spec 105)."""
    if staff.status == StaffStatus.ACTIVE and staff.role in OFFICE_NETWORK_ROLES:
        return None
    return ONLY_ADMIN_OFFICE_NETWORKS_MESSAGE


def why_cannot_manage_working_hours(staff: Staff) -> str | None:
    """Return why `staff` may not see everyone's hours, correct a day or set the Working Day.

    A Platform Admin in Support Access answers as an Admin, so may (spec 105a-108).
    """
    if staff.status == StaffStatus.ACTIVE and staff.role in WORKING_HOURS_ROLES:
        return None
    return ONLY_ADMIN_WORKING_HOURS_MESSAGE


def why_cannot_clock(staff: Staff) -> str | None:
    """Return why `staff` may not clock in or out, or None: every active Staff member may.

    A Platform Admin in Support Access is not Staff, so never clocks (issue 56).
    """
    if isinstance(staff, PlatformVisitor):
        return PLATFORM_CANT_CLOCK_MESSAGE
    if staff.status == StaffStatus.ACTIVE:
        return None
    return ONLY_ACTIVE_STAFF_CLOCK_MESSAGE


def clocks_from_any_network(staff: Staff) -> bool:
    """Whether `staff` clocks in and out from any network, by Role alone (issue 87)."""
    return staff.role in CLOCK_ANYWHERE_ROLES


def why_cannot_ask_work_from_home(staff: Staff) -> str | None:
    """Return why `staff` may not ask for Work From Home dates, or None (spec 132).

    Admins clock in anywhere, so never need them; a Platform Admin never clocks.
    """
    reason = why_cannot_clock(staff)
    if reason is not None:
        return reason
    if clocks_from_any_network(staff):
        return ADMIN_NEEDS_NO_WORK_FROM_HOME_MESSAGE
    return None


def why_cannot_manage_work_from_home(staff: Staff) -> str | None:
    """Return why `staff` may not approve, decline, set or end someone's dates, or None.

    Only an Admin may (spec 133); a Platform Admin in Support Access answers as an Admin.
    """
    if staff.status == StaffStatus.ACTIVE and staff.role in WORKING_HOURS_ROLES:
        return None
    return ONLY_ADMIN_WORK_FROM_HOME_MESSAGE


def why_cannot_use_inbox(staff: Staff) -> str | None:
    """Return why `staff` may not use the Inbox, or None: every Staff member may (spec 128).

    A conversation is private to its two people, so a Platform Admin in Support Access, who is
    not Staff, never reads or writes one (spec 129).
    """
    if isinstance(staff, PlatformVisitor):
        return PLATFORM_CANT_READ_INBOX_MESSAGE
    return None


def why_cannot_manage_commission(staff: Staff) -> str | None:
    """Return why `staff` may not see or change Commission, or None when they may (spec 76d)."""
    if staff.status == StaffStatus.ACTIVE and staff.role in COMMISSION_ROLES:
        return None
    return ONLY_ADMIN_COMMISSION_MESSAGE


class BatchAction(StrEnum):
    """What a Staff member may want to do with Batches (shown as "classes")."""

    SEE = "see"  # List and open classes; Teachers only their own (spec 22).
    CHANGE = "change"  # Create and edit classes, add and remove Students (spec 83-84).
    SEE_ENROLMENT = "see_enrolment"  # "Students and their classes" (spec 86a).


def why_cannot_on_batches(staff: Staff, action: BatchAction) -> str | None:
    """Return why `staff` may not do `action` with classes, or None when they may."""
    if staff.status != StaffStatus.ACTIVE or staff.role not in BATCH_ROLES:
        return BATCH_ROLES_MESSAGE
    if action is BatchAction.SEE or staff.role in BATCH_MANAGING_ROLES:
        return None
    return CHANGE_BATCHES_MESSAGE if action is BatchAction.CHANGE else PAPERWORK_ROLES_MESSAGE


def can_see_batch(staff: Staff, teacher_id: int) -> bool:
    """Return whether `staff` may open a class: any, for Admins and counsellors; a Teacher's own."""
    if why_cannot_on_batches(staff, BatchAction.SEE) is not None:
        return False
    return staff.role in BATCH_MANAGING_ROLES or staff.pk == teacher_id


def why_cannot_take_attendance(staff: Staff, teacher_id: int) -> str | None:
    """Return why `staff` may not mark a class's Attendance, or None when they may (spec 85).

    The class's own Teacher may, and so may any Admin. Counsellors only look.
    """
    if why_cannot_on_batches(staff, BatchAction.SEE) is not None:
        return BATCH_ROLES_MESSAGE
    if staff.role == Role.ADMIN or (staff.role == Role.TEACHER and staff.pk == teacher_id):
        return None
    return TAKE_ATTENDANCE_MESSAGE


def why_cannot_ask_transfer(staff: Staff, student: Student) -> str | None:
    """Return why `staff` may not ask to take `student`, or None when they may (spec 44a).

    Counsellors and Admins ask for a Student who isn't theirs, whoever counsels them, Lost ones
    too. A Platform Admin is not Staff, so can never become a Student's counsellor.
    """
    if isinstance(staff, PlatformVisitor):
        return PLATFORM_CANT_TAKE_MESSAGE
    if staff.role == Role.TEACHER:
        return TEACHERS_MESSAGE
    if staff.status != StaffStatus.ACTIVE or staff.role not in TRANSFER_ASKING_ROLES:
        return ASK_ROLES_MESSAGE
    return ALREADY_YOURS_MESSAGE if student.assigned_counsellor_id == staff.pk else None


def why_cannot_answer_transfer(staff: Staff, student: Student, asked_by_id: int) -> str | None:
    """Return why `staff` may not approve or decline a request for `student` (spec 44b).

    The Student's counsellor or an Admin answers; for a Student with no counsellor, Front Desk or
    an Admin. Nobody answers their own request. A Platform Admin answers as an Admin.
    """
    if staff.role == Role.TEACHER:
        return TEACHERS_MESSAGE
    if not isinstance(staff, PlatformVisitor) and staff.pk == asked_by_id:
        return OWN_REQUEST_MESSAGE
    if staff.status != StaffStatus.ACTIVE:
        return ANSWER_ASSIGNED_MESSAGE
    return None if staff.role == Role.ADMIN else _why_not_answer_without_admin(staff, student)


def _why_not_answer_without_admin(staff: Staff, student: Student) -> str | None:
    """For an active Staff member who isn't an Admin: their counsellor, or Front Desk if none."""
    if student.assigned_counsellor_id is None:
        return None if staff.role == Role.FRONT_DESK else ANSWER_UNASSIGNED_MESSAGE
    is_theirs = staff.role == Role.COUNSELLOR and student.assigned_counsellor_id == staff.pk
    return None if is_theirs else ANSWER_ASSIGNED_MESSAGE


def why_cannot_cancel_transfer(staff: Staff, asked_by_id: int) -> str | None:
    """Return why `staff` may not cancel a waiting request: only who asked may (spec 44c)."""
    if isinstance(staff, PlatformVisitor) or staff.pk != asked_by_id:
        return ONLY_REQUESTER_CANCELS_MESSAGE
    return None


def why_cannot_see_all_transfer_requests(staff: Staff) -> str | None:
    """Return why `staff` may not list everyone's Transfer Requests, or None (spec 44d)."""
    if staff.status == StaffStatus.ACTIVE and staff.role == Role.ADMIN:
        return None
    return ONLY_ADMIN_SEES_ALL_REQUESTS_MESSAGE


def _why_not(staff: Staff, action: StudentAction, student: Student) -> str | None:
    """The reason for an active Staff member of a Student Role; None when allowed."""
    if action is StudentAction.CHANGE:
        return _why_not_change(staff, student)
    if action is StudentAction.ASSIGN:
        if staff.role not in ASSIGNING_ROLES:
            return ASSIGN_ROLES_MESSAGE
        return None if student.assigned_counsellor_id is None else ALREADY_ASSIGNED_MESSAGE
    if action is StudentAction.TRANSFER and student.assigned_counsellor_id is None:
        return NOT_ASSIGNED_YET_MESSAGE
    # Edit details, the Stage, Transfer, Applications, Visas, Documents and Fees are the Student's
    # counsellor's work, or an Admin's.
    return _why_not_counsellor_or_admin(staff, student)


def _why_not_change(staff: Staff, student: Student) -> str | None:
    if staff.role == Role.FRONT_DESK:
        # Front Desk corrects a Lead's details until a Counsellor takes them on (spec 27).
        is_open_lead = student.assigned_counsellor_id is None and student.stage == Stage.LEAD
        return None if is_open_lead else FRONT_DESK_LEADS_ONLY_MESSAGE
    return _why_not_counsellor_or_admin(staff, student)


def _why_not_counsellor_or_admin(staff: Staff, student: Student) -> str | None:
    if staff.role == Role.ADMIN:
        return None
    if staff.role == Role.COUNSELLOR:
        return None if student.assigned_counsellor_id == staff.pk else NOT_YOUR_STUDENT_MESSAGE
    return COUNSELLOR_OR_ADMIN_MESSAGE


def why_cannot_manage_two_factor_of(by: Staff, person: Staff) -> str | None:
    """Return why `by` may not switch `person`'s two-step login or reset it, or None (issue 62).

    An Admin may, and a Platform Admin in Support Access answers as one. Nobody does it to
    themselves from Employees: an Admin switches their own off in My settings, with their
    password and a number, so someone who took over an Admin's session can't quietly remove it.
    """
    if by.status != StaffStatus.ACTIVE or by.role not in TWO_FACTOR_MANAGING_ROLES:
        return ONLY_ADMIN_TWO_FACTOR_MESSAGE
    if not isinstance(by, PlatformVisitor) and by.pk == person.pk:
        return OWN_TWO_FACTOR_MESSAGE
    return None


def why_cannot_switch_off_own_two_factor(staff: Staff) -> str | None:
    """Return why `staff` may not switch their own two-step login off, or None (issue 62).

    An Admin may, and so may someone who switched it on themselves. When an Admin (or the
    office's default) requires it, it stays on until an Admin switches it off. Anyone may switch
    their own on and make their own recovery codes.
    """
    if isinstance(staff, PlatformVisitor):
        return OWN_TWO_FACTOR_MESSAGE
    if staff.role == Role.ADMIN or staff.two_factor_chosen_by_self:
        return None
    return ADMIN_REQUIRES_TWO_FACTOR_MESSAGE
