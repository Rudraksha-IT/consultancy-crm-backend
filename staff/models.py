from __future__ import annotations

from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import BooleanField, ExpressionWrapper, Q
from django.utils import timezone

STAFF_NAME_MAX_LENGTH = 200
ROLE_MAX_LENGTH = 20
STATUS_MAX_LENGTH = 20
JOB_TITLE_MAX_LENGTH = 100
# "+" and 15 digits, the longest international number (E.164).
MOBILE_MAX_LENGTH = 16
COLOUR_MODE_MAX_LENGTH = 10


class Role(models.TextChoices):
    """The fixed job types that decide what a Staff member can see and do."""

    ADMIN = "admin", "Admin"
    COUNSELLOR = "counsellor", "Counsellor"
    FRONT_DESK = "front_desk", "Front Desk"
    TEACHER = "teacher", "Teacher"


class StaffStatus(models.TextChoices):
    """Where a Staff member's account is: invited (no password yet), active, or has left."""

    INVITED = "invited", "Invited, not joined yet"
    ACTIVE = "active", "Active"
    DEACTIVATED = "deactivated", "Has left"


class ColourMode(models.TextChoices):
    """A Staff member's own light or dark choice: light until they pick dark (2026-11-20)."""

    LIGHT = "light", "Light"
    DARK = "dark", "Dark"


class StaffManager(BaseUserManager["Staff"]):
    """Looks Staff up by email, which is how they log in."""


class Staff(AbstractBaseUser):
    """A person who works for the Consultancy and logs in with email and password."""

    name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=ROLE_MAX_LENGTH, choices=Role.choices)
    mobile = models.CharField(max_length=MOBILE_MAX_LENGTH, blank=True, default="")
    job_title = models.CharField(max_length=JOB_TITLE_MAX_LENGTH, blank=True, default="")
    joined_on = models.DateField(default=timezone.localdate)
    status = models.CharField(
        max_length=STATUS_MAX_LENGTH, choices=StaffStatus.choices, default=StaffStatus.ACTIVE
    )
    # Only an active Staff member can log in or use a token; always follows `status`.
    is_active = models.GeneratedField(
        expression=ExpressionWrapper(Q(status=StaffStatus.ACTIVE), output_field=BooleanField()),
        output_field=models.BooleanField(),
        db_persist=True,
    )
    # Personal Settings (issue 38): light/dark and which emails the app sends this person.
    colour_mode = models.CharField(
        max_length=COLOUR_MODE_MAX_LENGTH, choices=ColourMode.choices, default=ColourMode.LIGHT
    )
    email_on_task_given = models.BooleanField(default=True)
    email_on_task_due_tomorrow = models.BooleanField(default=True)
    email_morning_summary = models.BooleanField(default=False)
    # Swipe between pages (asked 2026-11-17, moved from Configuration): pull past the end of a
    # page to open the next one. Off until the person switches it on: it changed pages by accident.
    page_swipe = models.BooleanField(default=False)
    # Two-step login (issue 62): whether this person must enter a number from their app after the
    # password. New Staff start from STAFF_TWO_FACTOR_DEFAULT (staff.services.build_staff).
    two_factor_required = models.BooleanField(default=False)
    # Whether the person switched it on themselves in My settings, so may also switch it off; an
    # Admin's or the default's requirement is theirs to keep.
    two_factor_chosen_by_self = models.BooleanField(default=False)
    # Raised by every sign-out everywhere; tokens carrying an older value are refused.
    auth_version = models.PositiveIntegerField(default=0)

    objects = StaffManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["name", "role"]

    class Meta:
        verbose_name_plural = "staff"

    def __str__(self) -> str:
        return str(self.name)


# A Platform Admin inside a Consultancy has this id: no Staff row ever does (ids start at 1), so
# "is this mine?" checks never match and lists filtered by them are empty.
PLATFORM_VISITOR_ID = 0


class PlatformVisitorIsNotStaffError(Exception):
    """Something tried to store or delete a Platform Admin as if they were Staff."""


class PlatformVisitor(Staff):
    """A Platform Admin inside a Consultancy during Support Access (spec 9-11b, issue 27).

    Never a Staff account: it is built in memory for each request from the open Support Access
    visit, is never saved, and never appears in the Staff list. It answers the permission
    policy as an active Admin. Services store who did something through `staff.actors`, which
    keeps no Staff link for it and records the Platform Admin's own name instead.
    """

    platform_admin_id: int
    support_visit_id: int

    class Meta:
        proxy = True

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # Django's own signature
        """Refuse: a Platform Admin is never stored as Staff."""
        raise PlatformVisitorIsNotStaffError

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:  # noqa: ANN401
        """Refuse: there is no Staff row to delete."""
        raise PlatformVisitorIsNotStaffError
