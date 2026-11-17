from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower

from codes.models import CodeRecord
from tenancy.models import LOGO_CONTENT_TYPE_MAX_LENGTH, LOGO_KEY_MAX_LENGTH

PLATFORM_ADMIN_NAME_MAX_LENGTH = 200


class PlatformAdminManager(BaseUserManager["PlatformAdmin"]):
    """Looks Platform Admins up by email, which is how they log in."""


class PlatformAdmin(AbstractBaseUser):
    """A member of the team running the product; not Staff of any Consultancy."""

    name = models.CharField(max_length=PLATFORM_ADMIN_NAME_MAX_LENGTH)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    # Raised by every sign-out everywhere; tokens carrying an older value are refused.
    auth_version = models.PositiveIntegerField(default=0)
    # Wrong current passwords on the Account page, counted per window to limit them (issue 59).
    wrong_password_tries = models.PositiveSmallIntegerField(default=0)
    wrong_password_window_started_at = models.DateTimeField(null=True, blank=True)
    # Two-step login (issue 61): set from PLATFORM_ADMIN_TWO_FACTOR_DEFAULT when the deploy
    # creates the Platform Admin, then changed only on the Platform's Security page.
    two_factor_required = models.BooleanField(default=False)
    # When they chose their password (issue 65). Empty while invited from the Platform Admins
    # tab: they can't log in or reset a password until they join with their Code.
    joined_at = models.DateTimeField(null=True, blank=True)

    objects = PlatformAdminManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["name"]

    class Meta:
        # Two deploys racing with one new email still make only one Platform Admin (issue 59).
        constraints = (
            models.UniqueConstraint(Lower("email"), name="platform_admin_email_ci_unique"),
        )

    def __str__(self) -> str:
        return str(self.name)


class PlatformAdminChange(models.Model):
    """One creation of, or change to, a Platform Admin (issue 59). Never holds a password.

    `by` is empty when the deploy (the system) created the Platform Admin.
    """

    platform_admin = models.ForeignKey(
        PlatformAdmin, on_delete=models.PROTECT, related_name="changes"
    )
    by = models.ForeignKey(
        PlatformAdmin, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created = models.BooleanField(default=False)
    # The names of what changed: "name", "email" and "password", never their values.
    fields = models.JSONField(default=list)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("pk",)

    def __str__(self) -> str:
        return f"Platform Admin change {self.pk}"


SUPPORT_REASON_MAX_LENGTH = 500
# sha256 of an entry code, in hex.
ENTRY_CODE_HASH_LENGTH = 64
CONSULTANCY_STATUS_MAX_LENGTH = 20


class SupportVisit(models.Model):
    """One Support Access visit: a Platform Admin inside a Consultancy as themselves (spec 9-11b).

    Kept on the Platform side (public schema) and never shown to the Consultancy. The visit is
    open from `started_at` until it is left (`ended_at`) or until `ends_by`, whichever is first;
    its login tokens work only while it is open.
    """

    platform_admin = models.ForeignKey(PlatformAdmin, on_delete=models.PROTECT, related_name="+")
    consultancy = models.ForeignKey(
        "tenancy.Consultancy", on_delete=models.PROTECT, related_name="+"
    )
    reason = models.CharField(max_length=SUPPORT_REASON_MAX_LENGTH)
    # The Consultancy Status when the visit started, e.g. a Past due office being helped.
    consultancy_status = models.CharField(max_length=CONSULTANCY_STATUS_MAX_LENGTH)
    started_at = models.DateTimeField()
    ends_by = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    # The one-time code that carries the Platform Admin from the Platform to the office's own
    # address, where it is swapped for login tokens. Only its hash is kept; usable once, briefly.
    entry_code_hash = models.CharField(max_length=ENTRY_CODE_HASH_LENGTH, unique=True)
    entry_code_expires_at = models.DateTimeField()
    entered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at", "-pk")
        indexes = (models.Index(fields=("-started_at",), name="support_visit_newest_idx"),)

    def __str__(self) -> str:
        return f"Support visit {self.pk}"


class PlatformAdminCode(CodeRecord):
    """A Platform Admin's emailed Code (the Platform scope, issue 63).

    Kept on the Platform side (public schema) and used only on the Platform host; an office's
    Codes stay in its own schema, so the two never mix. Written only by `codes.services`.
    """

    platform_admin = models.ForeignKey(
        PlatformAdmin, on_delete=models.CASCADE, related_name="one_time_codes"
    )

    class Meta:
        indexes = (
            models.Index(
                fields=("platform_admin", "purpose", "created_at"), name="platform_code_recent_idx"
            ),
        )

    def __str__(self) -> str:
        return f"{self.purpose} code for platform admin {self.platform_admin_id}"


EMAIL_HASH_LENGTH = 64  # HMAC-SHA256 as hex


class PlatformResetRequest(models.Model):
    """One "Forgot password?" request on the Platform, by the email's keyed hash (issue 63).

    Kept for an hour to limit requests per email, whether or not it is a Platform Admin's.
    Never holds the email.
    """

    email_hash = models.CharField(max_length=EMAIL_HASH_LENGTH, db_index=True)
    requested_at = models.DateTimeField(db_index=True)

    def __str__(self) -> str:
        return f"Platform reset request {self.pk}"


class ProductBrand(models.Model):
    """The product's own logo (issue 65), shown on the Platform's and Central login's screens.

    One row only (pk 1). The file is in private storage under `platform/`; no key means no logo
    (the initials show). The version grows with every upload, so each logo has its own address.
    Written only by `platform_admin.brand`.
    """

    logo_key = models.CharField(max_length=LOGO_KEY_MAX_LENGTH, blank=True, default="")
    logo_content_type = models.CharField(
        max_length=LOGO_CONTENT_TYPE_MAX_LENGTH, blank=True, default=""
    )
    logo_version = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return "Product brand"
