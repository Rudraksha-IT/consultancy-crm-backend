"""In-app notifications: Tasks, Transfer Requests, the Bin and Work From Home dates.

Spec 80 and 44b, issues 79 and 87.

A Task given to someone or falling due; a Transfer Request asked for or answered; something put in
the Bin, restored or deleted; Work From Home dates asked for, answered or set.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from clock_in.models import WorkFromHome
from staff.models import STAFF_NAME_MAX_LENGTH, Staff
from students.models import Student
from tasks.models import Task
from transfer_requests.models import TransferRequest


class NotificationKind(models.TextChoices):
    """Why someone was notified."""

    ASSIGNED = "assigned"  # Someone else gave them a Task.
    DUE_TODAY = "due_today"  # One of their open Tasks is due today.
    OVERDUE = "overdue"  # One of their open Tasks is past its due date.
    TRANSFER_ASKED = "transfer_asked"  # Someone asked to take a Student they may answer for.
    TRANSFER_APPROVED = "transfer_approved"  # Their request was approved.
    TRANSFER_DECLINED = "transfer_declined"  # Their request was declined.
    BIN_ADDED = "bin_added"  # (Admins) Someone put a Document or a file in the Bin.
    BIN_RESTORED = "bin_restored"  # An Admin restored what they put in the Bin.
    BIN_DELETED = "bin_deleted"  # An Admin deleted for good what they put in the Bin.
    WORK_FROM_HOME_ASKED = "work_from_home_asked"  # (Admins) Someone asked for dates.
    WORK_FROM_HOME_APPROVED = "work_from_home_approved"  # An Admin approved their dates.
    WORK_FROM_HOME_DECLINED = "work_from_home_declined"  # An Admin declined their dates.
    WORK_FROM_HOME_SET = "work_from_home_set"  # An Admin set dates for them directly.


# The kinds about a Task, and those about a Transfer Request.
TASK_KINDS = (NotificationKind.ASSIGNED, NotificationKind.DUE_TODAY, NotificationKind.OVERDUE)
TRANSFER_KINDS = (
    NotificationKind.TRANSFER_ASKED,
    NotificationKind.TRANSFER_APPROVED,
    NotificationKind.TRANSFER_DECLINED,
)
BIN_KINDS = (
    NotificationKind.BIN_ADDED,
    NotificationKind.BIN_RESTORED,
    NotificationKind.BIN_DELETED,
)
WORK_FROM_HOME_KINDS = (
    NotificationKind.WORK_FROM_HOME_ASKED,
    NotificationKind.WORK_FROM_HOME_APPROVED,
    NotificationKind.WORK_FROM_HOME_DECLINED,
    NotificationKind.WORK_FROM_HOME_SET,
)
# The longest Bin item name kept: a file name and its type, e.g. "page-2.pdf of Passport".
BIN_ITEM_MAX_LENGTH = 300
BIN_REASON_MAX_LENGTH = 200


class Notification(models.Model):
    """One notification for one Staff member: a Task, a Transfer Request, the Bin or Work From Home.

    The words are built from the Task or request when shown, so nothing a person typed is stored
    twice. Each Task gives at most one notification of each kind. A Bin item may be deleted for
    good, so a Bin notification keeps its Student, what the item was called, who acted (as named
    then) and why it was put in the Bin (issue 79).
    """

    recipient = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=30, choices=NotificationKind.choices)
    task = models.ForeignKey(
        Task, on_delete=models.PROTECT, null=True, blank=True, related_name="notifications"
    )
    transfer_request = models.ForeignKey(
        TransferRequest,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="notifications",
    )
    student = models.ForeignKey(
        Student, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    work_from_home = models.ForeignKey(
        WorkFromHome,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="notifications",
    )
    bin_item = models.CharField(max_length=BIN_ITEM_MAX_LENGTH, blank=True, default="")
    bin_reason = models.CharField(max_length=BIN_REASON_MAX_LENGTH, blank=True, default="")
    # Who acted: put it in the Bin, or restored or deleted it. None for a Platform Admin.
    by_staff = models.ForeignKey(
        Staff, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    # When they read it; empty while it's unread.
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = (
            models.Index(fields=("recipient", "-created_at"), name="notifications_recipient"),
        )
        constraints = (
            models.UniqueConstraint(fields=("task", "kind"), name="notifications_one_per_kind"),
            models.CheckConstraint(
                condition=Q(kind__in=NotificationKind.values), name="notifications_known_kind"
            ),
            # A Task's kinds are about a Task only; a Transfer Request's about a request only.
            models.CheckConstraint(
                condition=(
                    Q(kind__in=TASK_KINDS, task__isnull=False, transfer_request__isnull=True)
                    | Q(kind__in=TRANSFER_KINDS, task__isnull=True, transfer_request__isnull=False)
                    | Q(
                        kind__in=BIN_KINDS,
                        task__isnull=True,
                        transfer_request__isnull=True,
                        student__isnull=False,
                    )
                    | Q(
                        kind__in=WORK_FROM_HOME_KINDS,
                        task__isnull=True,
                        transfer_request__isnull=True,
                        student__isnull=True,
                        work_from_home__isnull=False,
                    )
                ),
                name="notifications_about_one_thing",
            ),
        )

    def __str__(self) -> str:
        about = (
            self.task_id or self.transfer_request_id or self.student_id or self.work_from_home_id
        )
        return f"{self.kind}: {about}"
