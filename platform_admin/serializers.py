from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone
from rest_framework import serializers

from config.serializers import StrictCharField, StrictSerializer
from platform_admin.models import SUPPORT_REASON_MAX_LENGTH
from platform_admin.services import name_problem
from platform_admin.types import ConsultancyOnboarding
from staff.models import STAFF_NAME_MAX_LENGTH
from staff.serializers import EMAIL_MAX_LENGTH, PASSWORD_MAX_LENGTH, ResetCodeSerializer
from tenancy.models import CONSULTANCY_NAME_MAX_LENGTH, STATUS_REASON_MAX_LENGTH
from tenancy.serializers import ContactEmailField, ThemeSerializer
from tenancy.services import DATE_NOT_IN_FUTURE_MESSAGE, is_subdomain_taken
from tenancy.status import ConsultancyStatus
from tenancy.subdomains import find_subdomain_problem
from tenancy.theme import DEFAULT_THEME, theme_from_stored
from tenancy.types import StatusChange
from two_factor.serializers import NumberField

# Room for spaces around a 200-character name; the name is checked after trimming.
PLATFORM_ADMIN_NAME_INPUT_MAX_LENGTH = 250
SUBDOMAIN_TAKEN_MESSAGE = "This subdomain is already taken."
SUBDOMAIN_MAX_INPUT_LENGTH = 100
REQUIRED_MESSAGE = "This field is required."
NOT_USED_MESSAGE = "Not used for this Status."
STARTING_STATUSES = (ConsultancyStatus.EVALUATION.value, ConsultancyStatus.ACTIVE.value)
STATUS_CHOICES = tuple(value.value for value in ConsultancyStatus)


class FutureDateField(serializers.DateField):
    """A calendar date (YYYY-MM-DD) after today in the Consultancy's time zone."""

    def to_internal_value(self, data: object) -> date:
        """Refuse anything but a date string, and dates that aren't after today."""
        if not isinstance(data, str):
            raise serializers.ValidationError(
                self.error_messages["invalid"].format(format="YYYY-MM-DD")
            )
        day: date = super().to_internal_value(data)
        if day <= timezone.localdate():
            raise serializers.ValidationError(DATE_NOT_IN_FUTURE_MESSAGE)
        return day


def end_of_day(day: date) -> datetime:
    """Return the moment `day` ends in the Consultancy's time zone (midnight after it)."""
    return datetime.combine(
        day + timedelta(days=1), time.min, tzinfo=timezone.get_current_timezone()
    )


class LoginSerializer(serializers.Serializer[None]):
    """Email and password submitted on the Platform login screen."""

    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH)
    password = serializers.CharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)


class ConsultancyOnboardingSerializer(StrictSerializer[None]):
    """A new Consultancy and its starting Status, as entered by a Platform Admin.

    The starting Status is Evaluation (14 days) unless it is Active with a paid-until date.
    `contact_email` (required, ticket 20) becomes the office's contact email, where replies to
    every office email go.
    `subdomain` is only the front part of the address; the rest is PRODUCT_DOMAIN. No Admin:
    Admins are added once the office is set up (issue 64), so `admin_name`, `admin_email` and
    `admin_password` are refused as unknown fields.
    """

    name = StrictCharField(max_length=CONSULTANCY_NAME_MAX_LENGTH)
    subdomain = StrictCharField(max_length=SUBDOMAIN_MAX_INPUT_LENGTH)
    contact_email = ContactEmailField()
    theme = ThemeSerializer(required=False)
    status = serializers.ChoiceField(choices=STARTING_STATUSES, required=False)
    paid_until = FutureDateField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Require a paid-until date for Active, and refuse one for Evaluation."""
        is_active = attrs.get("status") == ConsultancyStatus.ACTIVE.value
        if is_active and "paid_until" not in attrs:
            raise serializers.ValidationError({"paid_until": [REQUIRED_MESSAGE]})
        if not is_active and "paid_until" in attrs:
            raise serializers.ValidationError({"paid_until": [NOT_USED_MESSAGE]})
        return attrs

    def validate_subdomain(self, value: str) -> str:
        """Reject subdomains that are malformed, reserved or already in use."""
        problem = find_subdomain_problem(value)
        if problem is not None:
            raise serializers.ValidationError(problem)
        if is_subdomain_taken(value):
            raise serializers.ValidationError(SUBDOMAIN_TAKEN_MESSAGE)
        return value

    def to_onboarding(self) -> ConsultancyOnboarding:
        """Return the validated details; no Theme means the default."""
        data = self.validated_data
        return ConsultancyOnboarding(
            name=data["name"],
            subdomain=data["subdomain"],
            theme=theme_from_stored(data["theme"]) if "theme" in data else DEFAULT_THEME,
            contact_email=data["contact_email"],
            status=ConsultancyStatus(data.get("status", ConsultancyStatus.EVALUATION.value)),
            paid_until=data.get("paid_until"),
        )


class StatusChangeSerializer(StrictSerializer[None]):
    """A Platform Admin's change of a Consultancy's Status.

    `evaluation` keeps the Consultancy in Evaluation and moves its end to the end of
    `evaluation_ends_at`; `active` needs `paid_until`. Neither date is taken for another Status.
    """

    status = serializers.ChoiceField(choices=STATUS_CHOICES)
    reason = StrictCharField(
        max_length=STATUS_REASON_MAX_LENGTH, required=False, allow_blank=True, default=""
    )
    paid_until = FutureDateField(required=False)
    evaluation_ends_at = FutureDateField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Require the date the chosen Status needs, and refuse the other one."""
        status = attrs["status"]
        needed = {
            ConsultancyStatus.ACTIVE.value: "paid_until",
            ConsultancyStatus.EVALUATION.value: "evaluation_ends_at",
        }.get(str(status))
        for field in ("paid_until", "evaluation_ends_at"):
            if field == needed and field not in attrs:
                raise serializers.ValidationError({field: [REQUIRED_MESSAGE]})
            if field != needed and field in attrs:
                raise serializers.ValidationError({field: [NOT_USED_MESSAGE]})
        return attrs

    def to_change(self) -> StatusChange:
        """Return the validated change; an evaluation end date ends when that day does."""
        data = self.validated_data
        ends_on: date | None = data.get("evaluation_ends_at")
        return StatusChange(
            status=ConsultancyStatus(data["status"]),
            reason=data["reason"],
            paid_until=data.get("paid_until"),
            evaluation_ends_at=None if ends_on is None else end_of_day(ends_on),
        )


# Long enough for any entry code this server makes (43 characters); nothing longer is read.
ENTRY_CODE_MAX_LENGTH = 100


class SupportVisitSerializer(StrictSerializer[None]):
    """Why a Platform Admin is opening a Consultancy (spec 10): required, kept on the visit."""

    reason = StrictCharField(max_length=SUPPORT_REASON_MAX_LENGTH)


class EntryCodeSerializer(StrictSerializer[None]):
    """The one-time code that enters a Support Access visit on the office's own address."""

    code = StrictCharField(max_length=ENTRY_CODE_MAX_LENGTH, trim_whitespace=False)


class AccountChangeSerializer(StrictSerializer[None]):
    """The Account page's name change (issue 65): no password; the email isn't changed here.

    Only the type and length are checked here; `save_platform_admin` holds the rules.
    """

    name = StrictCharField(max_length=PLATFORM_ADMIN_NAME_INPUT_MAX_LENGTH, allow_blank=True)


class AccountPasswordSerializer(StrictSerializer[None]):
    """The Account page's change password: the current one, then the new one (issue 59)."""

    current_password = StrictCharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)
    new_password = StrictCharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)
    # A current number from the app, needed once two-step login is set up (issue 61).
    code = NumberField(required=False)


class AddAdminSerializer(StrictSerializer[None]):
    """Add admin, on a Consultancy's row of the Platform list (issue 64): who to invite."""

    name = StrictCharField(max_length=STAFF_NAME_MAX_LENGTH)
    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH)

    def validate_name(self, value: str) -> str:
        """Allow letters with spaces, ' . and - between them; never markup or quotes."""
        problem = name_problem(value.strip())
        if problem is not None:
            raise serializers.ValidationError(problem)
        return value.strip()


class PlatformResetConfirmSerializer(ResetCodeSerializer):
    """Forgot password, last step on the Platform (issue 63): the email, Code and new password."""

    new_password = StrictCharField(max_length=PASSWORD_MAX_LENGTH, trim_whitespace=False)
