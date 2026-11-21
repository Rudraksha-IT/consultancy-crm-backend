"""Work Sessions, the Working Day, Work From Home dates and the records of changes.

Spec 103-110 and 131-133, issues 56 and 87. Times come from the server clock only. No address,
device or location is ever stored here (ADR 0004, spec 110). Work Session Changes and Working Day
Changes are only ever added.
"""

from __future__ import annotations

from typing import Any

from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from clock_in.exceptions import ClockInRecordIsAppendOnlyError
from config.public_ids import public_id_field
from staff.models import STAFF_NAME_MAX_LENGTH, Staff

DEFAULT_WORKING_DAY_MINUTES = 8 * 60
CHANGE_TEXT_MAX_LENGTH = 100
CORRECTION_REASON_MAX_LENGTH = 300
# There is one Working Day per Consultancy, kept in this row.
WORKING_DAY_ROW = 1


class WorkSession(models.Model):
    """One stretch of work by a Staff member, from clocking in to clocking out."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    clocked_in_at = models.DateTimeField()
    # Empty while the session is open, or when the clock-out was forgotten.
    clocked_out_at = models.DateTimeField(null=True)
    # Set when the person clocked in again while this one was still open (spec 109).
    not_clocked_out = models.BooleanField(default=False)
    # How an Admin corrected it (spec 108): added as missed time, or its clock-out was set.
    added_by_correction = models.BooleanField(default=False)
    clock_out_by_correction = models.BooleanField(default=False)
    # Clocked in on the person's Approved Work From Home dates: the day shows "Home" (issue 87).
    # Taken from the dates, never from the address.
    work_from_home = models.BooleanField(default=False)

    class Meta:
        ordering = ("clocked_in_at", "pk")
        indexes = (models.Index(fields=("staff", "clocked_in_at")),)
        constraints = (
            models.UniqueConstraint(
                fields=("staff",),
                condition=Q(clocked_out_at__isnull=True, not_clocked_out=False),
                name="clock_in_one_open_work_session",
            ),
            models.CheckConstraint(
                condition=Q(clocked_out_at__isnull=True)
                | Q(clocked_out_at__gte=F("clocked_in_at")),
                name="clock_in_out_not_before_in",
            ),
        )

    def __str__(self) -> str:
        return f"Work Session {self.pk}"

    @property
    def corrected(self) -> bool:
        """Whether an Admin added it or set its clock-out."""
        return self.added_by_correction or self.clock_out_by_correction


class ClockInAppendOnlyQuerySet[M: models.Model](models.QuerySet[M]):
    """Refuses bulk changes and bulk deletes."""

    def update(self, **kwargs: Any) -> int:  # noqa: ANN401  # Django's own signature
        """Refuse: a record is never changed."""
        raise ClockInRecordIsAppendOnlyError

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse: a record is never deleted."""
        raise ClockInRecordIsAppendOnlyError


class AppendOnlyRecord(models.Model):
    """A record that is only ever added: who did it and when, kept as it was."""

    at = models.DateTimeField(default=timezone.now)
    # A Platform Admin is never linked as Staff; their name is kept, marked (issue 27).
    by_staff = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    by_platform_admin = models.BooleanField(default=False)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # Django's own signature
        """Add a new record; refuse to change one already stored."""
        if self.pk is not None:
            raise ClockInRecordIsAppendOnlyError
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:  # noqa: ANN401
        """Refuse: a record is never deleted."""
        raise ClockInRecordIsAppendOnlyError


class CorrectionKind(models.TextChoices):
    """What an Admin's correction did."""

    CLOCK_OUT_SET = "clock_out_set", "Set the missing clock-out"
    TIME_ADDED = "time_added", "Added missed time"


class WorkSessionChange(AppendOnlyRecord):
    """One Admin correction of one day: before, after and why (spec 108)."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    work_session = models.ForeignKey(WorkSession, on_delete=models.PROTECT, related_name="+")
    # Whose day it was, and which day.
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    day = models.DateField()
    kind = models.CharField(max_length=20, choices=CorrectionKind.choices)
    before = models.CharField(max_length=CHANGE_TEXT_MAX_LENGTH)
    after = models.CharField(max_length=CHANGE_TEXT_MAX_LENGTH)
    reason = models.TextField(max_length=CORRECTION_REASON_MAX_LENGTH)

    objects = ClockInAppendOnlyQuerySet["WorkSessionChange"].as_manager()

    class Meta:
        ordering = ("day", "at", "pk")
        indexes = (models.Index(fields=("staff", "day")),)

    def __str__(self) -> str:
        return f"{self.kind} {self.day}"


class WeeklyOff(models.TextChoices):
    """The office's weekly days off (asked 2026-11-17); Saturday, as offices in Nepal close."""

    SATURDAY = "saturday", "Saturday"
    SUNDAY = "sunday", "Sunday"
    BOTH = "both", "Saturday and Sunday"
    NONE = "none", "None"


WEEKLY_OFF_MAX_LENGTH = 8


class WorkingDay(models.Model):
    """How long a full day of work is, in minutes, and the weekly days off; one row (spec 105a)."""

    minutes = models.PositiveSmallIntegerField(default=DEFAULT_WORKING_DAY_MINUTES)
    weekly_off = models.CharField(
        max_length=WEEKLY_OFF_MAX_LENGTH, choices=WeeklyOff.choices, default=WeeklyOff.SATURDAY
    )

    def __str__(self) -> str:
        return f"{self.minutes} minutes"


class WorkingDayChange(AppendOnlyRecord):
    """One change of the Working Day: from, to, who and when (spec 105a)."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    old_minutes = models.PositiveSmallIntegerField()
    new_minutes = models.PositiveSmallIntegerField()

    objects = ClockInAppendOnlyQuerySet["WorkingDayChange"].as_manager()

    class Meta:
        ordering = ("-at", "-pk")

    def __str__(self) -> str:
        return f"{self.old_minutes} to {self.new_minutes}"


HOLIDAY_NAME_MAX_LENGTH = 60


class Holiday(models.Model):
    """A day the office is closed, set by an Admin in Configuration (asked 2026-11-16).

    Like Saturday it is off: never "not in", never a short day, never corrected. Anyone who works
    can still clock in.
    """

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    day = models.DateField(unique=True)
    name = models.CharField(max_length=HOLIDAY_NAME_MAX_LENGTH)

    class Meta:
        ordering = ("day",)

    def __str__(self) -> str:
        return f"{self.day} {self.name}"


WORK_FROM_HOME_REASON_MAX_LENGTH = 200


class WorkFromHomeStatus(models.TextChoices):
    """Where a set of Work From Home dates is. Only Approved dates give permission."""

    WAITING = "waiting", "Waiting"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"
    ENDED = "ended", "Ended"


class WorkFromHome(models.Model):
    """Dates on which one Staff member may clock in from any network (spec 131-133, issue 87).

    Asked for by the Staff member (Waiting until an Admin answers) or set directly by an Admin
    (Approved at once). An empty end lasts until an Admin ends it.
    """

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    starts_on = models.DateField()
    ends_on = models.DateField(null=True)
    reason = models.CharField(max_length=WORK_FROM_HOME_REASON_MAX_LENGTH)
    status = models.CharField(max_length=10, choices=WorkFromHomeStatus.choices)
    asked_at = models.DateTimeField(default=timezone.now)
    # Who asked or set them; a Platform Admin is never linked as Staff (issue 27).
    asked_by_staff = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    asked_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    asked_by_platform_admin = models.BooleanField(default=False)
    # Who approved or declined them, and when.
    answered_at = models.DateTimeField(null=True)
    answered_by_staff = models.ForeignKey(
        Staff, on_delete=models.PROTECT, null=True, related_name="+"
    )
    answered_by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, blank=True, default="")
    answered_by_platform_admin = models.BooleanField(default=False)

    class Meta:
        ordering = ("-starts_on", "-pk")
        indexes = (models.Index(fields=("staff", "status")),)
        constraints = (
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=F("starts_on")),
                name="clock_in_work_from_home_end_not_before_start",
            ),
            models.CheckConstraint(
                condition=Q(status__in=WorkFromHomeStatus.values),
                name="clock_in_work_from_home_known_status",
            ),
            models.CheckConstraint(
                condition=~Q(reason=""), name="clock_in_work_from_home_has_reason"
            ),
        )

    def __str__(self) -> str:
        return f"Work From Home {self.pk}"

    @property
    def set_directly(self) -> bool:
        """Whether an Admin set them, rather than the person asking."""
        return self.asked_by_staff_id != self.staff_id
