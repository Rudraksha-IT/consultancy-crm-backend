"""The one permission policy: can this Staff member do this action on this Student? (issue 09)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from access.policy import (
    ALREADY_ASSIGNED_MESSAGE,
    ASSIGN_ROLES_MESSAGE,
    COUNSELLOR_OR_ADMIN_MESSAGE,
    FRONT_DESK_LEADS_ONLY_MESSAGE,
    NOT_ASSIGNED_YET_MESSAGE,
    NOT_YOUR_STUDENT_MESSAGE,
    ONLY_ADMIN_COMMISSION_MESSAGE,
    ONLY_ADMIN_EXPENSES_MESSAGE,
    ONLY_ADMIN_SEES_PAYMENTS_MESSAGE,
    RECEIPT_WINDOW,
    TEACHERS_MESSAGE,
    StudentAction,
    can_act_on_student,
    why_cannot,
    why_cannot_change,
    why_cannot_manage_commission,
    why_cannot_manage_expenses,
    why_cannot_see_payment,
)
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student

WORKS_WITH_STUDENTS = (Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK)


def staff_member(role: Role, pk: int = 1, status: StaffStatus = StaffStatus.ACTIVE) -> Staff:
    """An unsaved Staff member with this Role."""
    return Staff(pk=pk, name=f"Test {role.label}", email=f"{pk}@abc.test", role=role, status=status)


def student(counsellor: Staff | None = None, stage: Stage = Stage.LEAD) -> Student:
    """An unsaved Student, assigned to `counsellor` if given."""
    return Student(pk=7, name="Anish Neupane", stage=stage, assigned_counsellor=counsellor)


class TestSeeingAndAdding:
    @pytest.mark.parametrize("role", WORKS_WITH_STUDENTS)
    @pytest.mark.parametrize("action", [StudentAction.SEE, StudentAction.ADD])
    def test_every_role_that_works_with_students_may(
        self, role: Role, action: StudentAction
    ) -> None:
        assert can_act_on_student(staff_member(role), action)

    @pytest.mark.parametrize("action", list(StudentAction))
    def test_a_teacher_may_do_nothing_with_students(self, action: StudentAction) -> None:
        assert not can_act_on_student(staff_member(Role.TEACHER), action, student())

    @pytest.mark.parametrize("status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    @pytest.mark.parametrize("action", list(StudentAction))
    def test_someone_not_active_may_do_nothing(
        self, status: StaffStatus, action: StudentAction
    ) -> None:
        admin = staff_member(Role.ADMIN, status=status)

        assert not can_act_on_student(admin, action, student())

    def test_seeing_one_student_is_allowed_whoever_they_are_assigned_to(self) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        assert can_act_on_student(staff_member(Role.COUNSELLOR), StudentAction.SEE, student(other))


class TestChanging:
    def test_an_admin_may_change_any_student(self) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        assert can_act_on_student(staff_member(Role.ADMIN), StudentAction.CHANGE, student(other))
        assert can_act_on_student(staff_member(Role.ADMIN), StudentAction.CHANGE, student())

    def test_the_assigned_counsellor_may_change_their_student(self) -> None:
        me = staff_member(Role.COUNSELLOR)

        assert can_act_on_student(me, StudentAction.CHANGE, student(me))

    def test_a_counsellor_may_not_change_someone_elses_student(self) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        assert not can_act_on_student(
            staff_member(Role.COUNSELLOR), StudentAction.CHANGE, student(other)
        )

    def test_a_counsellor_may_not_change_an_unassigned_student(self) -> None:
        assert not can_act_on_student(
            staff_member(Role.COUNSELLOR), StudentAction.CHANGE, student()
        )

    def test_front_desk_may_change_a_lead_with_no_counsellor_yet(self) -> None:
        assert can_act_on_student(staff_member(Role.FRONT_DESK), StudentAction.CHANGE, student())

    def test_front_desk_may_not_change_an_assigned_student(self) -> None:
        counsellor = staff_member(Role.COUNSELLOR, pk=2)

        assert not can_act_on_student(
            staff_member(Role.FRONT_DESK), StudentAction.CHANGE, student(counsellor)
        )

    def test_front_desk_may_not_change_an_unassigned_student_past_lead(self) -> None:
        unassigned = student(stage=Stage.COUNSELLING)

        assert not can_act_on_student(
            staff_member(Role.FRONT_DESK), StudentAction.CHANGE, unassigned
        )

    @pytest.mark.parametrize("role", list(Role))
    def test_changing_needs_a_student(self, role: Role) -> None:
        assert not can_act_on_student(staff_member(role), StudentAction.CHANGE)


class TestWhyNotChange:
    """The short reason a 403 gives (ticket 10: reasons like "not your Student")."""

    def test_there_is_no_reason_when_the_change_is_allowed(self) -> None:
        me = staff_member(Role.COUNSELLOR)

        assert why_cannot_change(me, student(me)) is None
        assert why_cannot_change(staff_member(Role.ADMIN), student()) is None
        assert why_cannot_change(staff_member(Role.FRONT_DESK), student()) is None

    def test_a_counsellor_is_told_it_is_not_their_student(self) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot_change(staff_member(Role.COUNSELLOR), student(other)) == (
            NOT_YOUR_STUDENT_MESSAGE
        )
        assert why_cannot_change(staff_member(Role.COUNSELLOR), student()) == (
            NOT_YOUR_STUDENT_MESSAGE
        )

    def test_front_desk_is_told_they_change_only_unassigned_leads(self) -> None:
        counsellor = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot_change(staff_member(Role.FRONT_DESK), student(counsellor)) == (
            FRONT_DESK_LEADS_ONLY_MESSAGE
        )

    def test_a_teacher_is_told_they_cannot_see_students(self) -> None:
        assert why_cannot_change(staff_member(Role.TEACHER), student()) == TEACHERS_MESSAGE

    def test_someone_not_active_gets_the_not_your_student_reason(self) -> None:
        admin = staff_member(Role.ADMIN, status=StaffStatus.DEACTIVATED)

        assert why_cannot_change(admin, student()) == NOT_YOUR_STUDENT_MESSAGE


class TestSettingTheStage:
    """Issue 11: only the Assigned Counsellor or an Admin moves a Student's Stage (spec 33, 38)."""

    def test_the_assigned_counsellor_and_an_admin_may(self) -> None:
        me = staff_member(Role.COUNSELLOR)

        assert can_act_on_student(me, StudentAction.SET_STAGE, student(me))
        assert can_act_on_student(staff_member(Role.ADMIN), StudentAction.SET_STAGE, student())

    def test_another_counsellor_is_told_it_is_not_their_student(self) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot(
            staff_member(Role.COUNSELLOR), StudentAction.SET_STAGE, student(other)
        ) == (NOT_YOUR_STUDENT_MESSAGE)

    def test_front_desk_may_not_even_for_an_unassigned_lead(self) -> None:
        assert why_cannot(staff_member(Role.FRONT_DESK), StudentAction.SET_STAGE, student()) == (
            COUNSELLOR_OR_ADMIN_MESSAGE
        )


class TestEditingDetails:
    """Issue 81: only the Assigned Counsellor or an Admin opens Edit details (spec 122)."""

    def test_the_assigned_counsellor_and_an_admin_may(self) -> None:
        me = staff_member(Role.COUNSELLOR)

        assert can_act_on_student(me, StudentAction.EDIT_DETAILS, student(me))
        assert can_act_on_student(staff_member(Role.ADMIN), StudentAction.EDIT_DETAILS, student())

    def test_another_counsellor_is_told_it_is_not_their_student(self) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot(
            staff_member(Role.COUNSELLOR), StudentAction.EDIT_DETAILS, student(other)
        ) == (NOT_YOUR_STUDENT_MESSAGE)

    def test_front_desk_may_not_even_for_an_open_lead(self) -> None:
        front_desk = staff_member(Role.FRONT_DESK)

        assert can_act_on_student(front_desk, StudentAction.CHANGE, student())
        assert why_cannot(front_desk, StudentAction.EDIT_DETAILS, student()) == (
            COUNSELLOR_OR_ADMIN_MESSAGE
        )

    def test_editing_details_needs_a_student(self) -> None:
        assert not can_act_on_student(staff_member(Role.ADMIN), StudentAction.EDIT_DETAILS)


class TestAssigning:
    """Issue 11: Admin or Front Desk assign a Counsellor to an unassigned Student (spec 28, 44)."""

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.FRONT_DESK])
    def test_admin_and_front_desk_may_assign_an_unassigned_student(self, role: Role) -> None:
        unassigned = student(stage=Stage.COUNSELLING)

        assert why_cannot(staff_member(role), StudentAction.ASSIGN, unassigned) is None

    def test_a_counsellor_may_not_claim_a_student(self) -> None:
        assert why_cannot(staff_member(Role.COUNSELLOR), StudentAction.ASSIGN, student()) == (
            ASSIGN_ROLES_MESSAGE
        )

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.FRONT_DESK])
    def test_a_student_with_a_counsellor_is_transferred_instead(self, role: Role) -> None:
        counsellor = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot(staff_member(role), StudentAction.ASSIGN, student(counsellor)) == (
            ALREADY_ASSIGNED_MESSAGE
        )


class TestTransferring:
    """Issue 11: the Assigned Counsellor or an Admin Transfers a Student (spec 42, 43)."""

    def test_the_assigned_counsellor_and_an_admin_may(self) -> None:
        me = staff_member(Role.COUNSELLOR)

        assert why_cannot(me, StudentAction.TRANSFER, student(me)) is None
        assert why_cannot(staff_member(Role.ADMIN), StudentAction.TRANSFER, student(me)) is None

    def test_another_counsellor_may_not(self) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot(
            staff_member(Role.COUNSELLOR), StudentAction.TRANSFER, student(other)
        ) == (NOT_YOUR_STUDENT_MESSAGE)

    def test_front_desk_may_not(self) -> None:
        counsellor = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot(
            staff_member(Role.FRONT_DESK), StudentAction.TRANSFER, student(counsellor)
        ) == (COUNSELLOR_OR_ADMIN_MESSAGE)

    def test_a_student_with_no_counsellor_is_assigned_instead(self) -> None:
        assert why_cannot(staff_member(Role.ADMIN), StudentAction.TRANSFER, student()) == (
            NOT_ASSIGNED_YET_MESSAGE
        )

    def test_a_teacher_is_told_they_cannot_see_students(self) -> None:
        assert why_cannot(staff_member(Role.TEACHER), StudentAction.TRANSFER, student()) == (
            TEACHERS_MESSAGE
        )


class TestWhyNotForListActions:
    @pytest.mark.parametrize("action", [StudentAction.SEE, StudentAction.ADD])
    def test_seeing_and_adding_have_no_reason_against_them(self, action: StudentAction) -> None:
        assert why_cannot(staff_member(Role.FRONT_DESK), action, student()) is None


class TestDocuments:
    """Issue 16: their counsellor or an Admin uploads; counsellors and Admins see all paperwork."""

    def test_the_assigned_counsellor_and_an_admin_may_upload(self) -> None:
        me = staff_member(Role.COUNSELLOR)

        assert can_act_on_student(me, StudentAction.CHANGE_DOCUMENTS, student(me))
        assert can_act_on_student(
            staff_member(Role.ADMIN, pk=3), StudentAction.CHANGE_DOCUMENTS, student(me)
        )

    def test_another_counsellor_may_not_upload(self) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot(
            staff_member(Role.COUNSELLOR), StudentAction.CHANGE_DOCUMENTS, student(other)
        ) == (NOT_YOUR_STUDENT_MESSAGE)

    def test_front_desk_may_not_upload_even_for_an_unassigned_lead(self) -> None:
        assert why_cannot(
            staff_member(Role.FRONT_DESK), StudentAction.CHANGE_DOCUMENTS, student()
        ) == (COUNSELLOR_OR_ADMIN_MESSAGE)

    def test_uploading_needs_a_student(self) -> None:
        assert not can_act_on_student(staff_member(Role.ADMIN), StudentAction.CHANGE_DOCUMENTS)

    @pytest.mark.parametrize(
        ("role", "may"),
        [
            (Role.ADMIN, True),
            (Role.COUNSELLOR, True),
            (Role.FRONT_DESK, False),
            (Role.TEACHER, False),
        ],
    )
    def test_the_documents_page_is_for_counsellors_and_admins(self, role: Role, may: bool) -> None:
        assert can_act_on_student(staff_member(role), StudentAction.SEE_PAPERWORK) is may

    @pytest.mark.parametrize(
        ("role", "may"),
        [
            (Role.ADMIN, True),
            (Role.COUNSELLOR, True),
            (Role.FRONT_DESK, False),
            (Role.TEACHER, False),
        ],
    )
    def test_progress_on_the_homes_is_for_counsellors_and_admins(
        self, role: Role, may: bool
    ) -> None:
        assert can_act_on_student(staff_member(role), StudentAction.SEE_PROGRESS) is may


class TestPayments:
    @pytest.mark.parametrize(
        ("role", "may"),
        [
            (Role.ADMIN, True),
            (Role.COUNSELLOR, True),
            (Role.FRONT_DESK, True),
            (Role.TEACHER, False),
        ],
    )
    def test_every_student_role_but_teacher_records_payments(self, role: Role, may: bool) -> None:
        other = staff_member(Role.COUNSELLOR, pk=2)

        allowed = can_act_on_student(
            staff_member(role), StudentAction.RECORD_PAYMENT, student(other)
        )

        assert allowed is may

    def test_someone_not_active_may_not_record(self) -> None:
        gone = staff_member(Role.FRONT_DESK, status=StaffStatus.DEACTIVATED)

        assert not can_act_on_student(gone, StudentAction.RECORD_PAYMENT, student())


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


class TestSeeingAPayment:
    def test_an_admin_sees_any_payment_however_old(self) -> None:
        admin = staff_member(Role.ADMIN, pk=3)

        assert why_cannot_see_payment(admin, 9, NOW - timedelta(days=400), NOW) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_the_recorder_sees_it_for_an_hour(self, role: Role) -> None:
        me = staff_member(role)

        assert why_cannot_see_payment(me, me.pk, NOW - RECEIPT_WINDOW, NOW) is None
        assert why_cannot_see_payment(
            me, me.pk, NOW - RECEIPT_WINDOW - timedelta(seconds=1), NOW
        ) == (ONLY_ADMIN_SEES_PAYMENTS_MESSAGE)

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK])
    def test_others_never_see_someone_elses(self, role: Role) -> None:
        assert why_cannot_see_payment(staff_member(role), 2, NOW, NOW) == (
            ONLY_ADMIN_SEES_PAYMENTS_MESSAGE
        )

    def test_a_teacher_is_told_they_cannot_see_students(self) -> None:
        teacher = staff_member(Role.TEACHER)

        assert why_cannot_see_payment(teacher, teacher.pk, NOW, NOW) == TEACHERS_MESSAGE

    def test_someone_not_active_sees_nothing(self) -> None:
        gone = staff_member(Role.ADMIN, status=StaffStatus.DEACTIVATED)

        assert why_cannot_see_payment(gone, gone.pk, NOW, NOW) == ONLY_ADMIN_SEES_PAYMENTS_MESSAGE


class TestManagingPayments:
    """Issue 19: only an Admin sees every Payment, and changes or deletes one."""

    @pytest.mark.parametrize(
        ("role", "may"),
        [
            (Role.ADMIN, True),
            (Role.COUNSELLOR, False),
            (Role.FRONT_DESK, False),
            (Role.TEACHER, False),
        ],
    )
    def test_only_an_admin_manages_payments(self, role: Role, may: bool) -> None:
        assert can_act_on_student(staff_member(role), StudentAction.MANAGE_PAYMENTS) is may

    def test_a_deactivated_admin_may_not(self) -> None:
        gone = staff_member(Role.ADMIN, status=StaffStatus.DEACTIVATED)

        assert not can_act_on_student(gone, StudentAction.MANAGE_PAYMENTS)


class TestVisits:
    """Issue 34: Front Desk, counsellors and Admins log any Student's Visits; Teachers don't."""

    @pytest.mark.parametrize(
        ("role", "may"),
        [
            (Role.ADMIN, True),
            (Role.COUNSELLOR, True),
            (Role.FRONT_DESK, True),
            (Role.TEACHER, False),
        ],
    )
    def test_who_may_log_visits(self, role: Role, may: bool) -> None:
        assert can_act_on_student(staff_member(role), StudentAction.LOG_VISIT) is may

    def test_a_deactivated_staff_member_may_not(self) -> None:
        gone = staff_member(Role.FRONT_DESK, status=StaffStatus.DEACTIVATED)

        assert not can_act_on_student(gone, StudentAction.LOG_VISIT)


class TestFees:
    """Issue 35: a Student's counsellor or an Admin changes their Fees; only Admins see Balances."""

    def test_the_students_counsellor_may_change_their_fees(self) -> None:
        counsellor = staff_member(Role.COUNSELLOR, pk=2)

        assert can_act_on_student(counsellor, StudentAction.CHANGE_FEES, student(counsellor))

    def test_another_counsellor_may_not(self) -> None:
        mine = staff_member(Role.COUNSELLOR, pk=2)

        assert why_cannot(
            staff_member(Role.COUNSELLOR, pk=3), StudentAction.CHANGE_FEES, student(mine)
        ) == ("Not your Student. Only their counsellor or an Admin can change them.")

    @pytest.mark.parametrize(
        ("role", "may"),
        [
            (Role.ADMIN, True),
            (Role.COUNSELLOR, False),
            (Role.FRONT_DESK, False),
            (Role.TEACHER, False),
        ],
    )
    def test_who_changes_any_students_fees(self, role: Role, may: bool) -> None:
        other = staff_member(Role.COUNSELLOR, pk=9)

        assert (
            can_act_on_student(staff_member(role), StudentAction.CHANGE_FEES, student(other)) is may
        )

    def test_changing_fees_needs_a_student(self) -> None:
        assert not can_act_on_student(staff_member(Role.ADMIN), StudentAction.CHANGE_FEES)

    @pytest.mark.parametrize(
        ("role", "may"),
        [
            (Role.ADMIN, True),
            (Role.COUNSELLOR, False),
            (Role.FRONT_DESK, False),
            (Role.TEACHER, False),
        ],
    )
    def test_only_an_admin_sees_balances(self, role: Role, may: bool) -> None:
        assert can_act_on_student(staff_member(role), StudentAction.SEE_BALANCES) is may

    def test_a_deactivated_admin_sees_no_balances(self) -> None:
        gone = staff_member(Role.ADMIN, status=StaffStatus.DEACTIVATED)

        assert not can_act_on_student(gone, StudentAction.SEE_BALANCES)


class TestExpenses:
    """Issue 36: only an active Admin sees, adds, changes and deletes Expenses (spec 76c)."""

    def test_an_admin_may(self) -> None:
        assert why_cannot_manage_expenses(staff_member(Role.ADMIN)) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_no_other_role_may(self, role: Role) -> None:
        assert why_cannot_manage_expenses(staff_member(role)) == ONLY_ADMIN_EXPENSES_MESSAGE

    @pytest.mark.parametrize("status", [StaffStatus.INVITED, StaffStatus.DEACTIVATED])
    def test_an_admin_who_is_not_active_may_not(self, status: StaffStatus) -> None:
        admin = staff_member(Role.ADMIN, status=status)

        assert why_cannot_manage_expenses(admin) == ONLY_ADMIN_EXPENSES_MESSAGE


class TestCommission:
    """Issue 37: only an active Admin sees, adds and changes Commission (spec 76d)."""

    def test_an_admin_may(self) -> None:
        assert why_cannot_manage_commission(staff_member(Role.ADMIN)) is None

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_no_other_role_may(self, role: Role) -> None:
        assert why_cannot_manage_commission(staff_member(role)) == ONLY_ADMIN_COMMISSION_MESSAGE

    def test_a_deactivated_admin_may_not(self) -> None:
        admin = staff_member(Role.ADMIN, status=StaffStatus.DEACTIVATED)

        assert why_cannot_manage_commission(admin) == ONLY_ADMIN_COMMISSION_MESSAGE
