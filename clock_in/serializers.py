"""What the Clock-in endpoints accept. The server never trusts the browser (issues README).

Times are never accepted for clocking in or out: those come from the server clock. Corrections
take a time of day ("17:30") on the day being corrected, in the Consultancy's time zone.
"""

from __future__ import annotations

import re
from datetime import date, time, timedelta
from typing import Any
from uuid import UUID

from django.utils import timezone
from rest_framework import serializers

from clock_in.models import (
    CORRECTION_REASON_MAX_LENGTH,
    HOLIDAY_NAME_MAX_LENGTH,
    WORK_FROM_HOME_REASON_MAX_LENGTH,
    WeeklyOff,
)
from clock_in.work_from_home import NewDates
from config.serializers import (
    NOT_A_RECORD_ID_MESSAGE,
    RecordIdField,
    StrictCharField,
    StrictSerializer,
    parse_record_id,
)
from students.serializers import NOTE_CHARACTERS_MESSAGE, NOTE_CONTROL_CHARACTERS

MONTH_MESSAGE = "Choose a month such as 2026-10."
DAY_MESSAGE = "Choose a day such as 2026-11-04."
TIME_MESSAGE = "Enter a time such as 17:30."
REASON_MESSAGE = "Say why you're correcting it."
STAFF_MESSAGE = "Choose an employee from the list."
WORKING_DAY_MESSAGE = "Enter between 4 and 12 hours, in half hours."
WORK_FROM_HOME_REASON_MESSAGE = "Say why."
START_BEFORE_TODAY_MESSAGE = "The start can't be before today."
START_TOO_LATE_MESSAGE = "Choose a start within a year."
END_BEFORE_START_MESSAGE = "The end date must be on or after the start date."
HOLIDAY_NAME_MESSAGE = "Give the holiday a name."
# A holiday is added and removed as a group of days: at most a month of them (ticket 17).
HOLIDAY_GROUP_MAX = 31
HOLIDAY_DAYS_MESSAGE = f"Choose between 1 and {HOLIDAY_GROUP_MAX} days."
HOLIDAY_DAYS_ONCE_MESSAGE = "Choose each day once."
HOLIDAY_IDS_MESSAGE = f"Choose between 1 and {HOLIDAY_GROUP_MAX} holidays."
HOLIDAY_IDS_ONCE_MESSAGE = "Choose each holiday once."
ONE_LINE_MESSAGE = "Use letters, numbers and punctuation only."
# A holiday's name is one line: no control characters at all, line breaks included.
_ANY_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Work From Home dates may start at most this far ahead.
WORK_FROM_HOME_AHEAD = timedelta(days=366)

FIRST_YEAR, LAST_YEAR = 2000, 2100
WORKING_DAY_MIN, WORKING_DAY_MAX, WORKING_DAY_STEP = 4 * 60, 12 * 60, 30
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_ALL_MESSAGES = ("required", "null", "blank", "invalid", "max_length")


def _same_message(message: str) -> dict[str, Any]:
    return dict.fromkeys(_ALL_MESSAGES, message)


class MonthField(StrictCharField):
    """A month as YYYY-MM, from 2000 to 2100; given back as (year, month)."""

    def __init__(self, *, required: bool = False) -> None:
        super().__init__(
            required=required, max_length=None, error_messages=_same_message(MONTH_MESSAGE)
        )

    def to_internal_value(self, data: object) -> tuple[int, int]:  # type: ignore[override]
        """Return (year, month), or refuse with one plain message."""
        if not isinstance(data, str):
            self.fail("invalid")
        found = _MONTH.match(super().to_internal_value(data))
        if found is None or not FIRST_YEAR <= int(found[1]) <= LAST_YEAR:
            self.fail("invalid")
        return int(found[1]), int(found[2])


class DayField(StrictCharField):
    """A calendar day as YYYY-MM-DD."""

    def __init__(self) -> None:
        super().__init__(max_length=None, error_messages=_same_message(DAY_MESSAGE))

    def to_internal_value(self, data: object) -> date:  # type: ignore[override]
        """Return the day, or refuse with one plain message."""
        if not isinstance(data, str) or not _DAY.match(data):
            self.fail("invalid")
        try:
            day = date.fromisoformat(data)
        except ValueError:
            # self.fail raises the field's validation error
            self.fail("invalid")
        if not FIRST_YEAR <= day.year <= LAST_YEAR:
            self.fail("invalid")
        return day


class TimeOfDayField(StrictCharField):
    """A time of day as HH:MM (24 hours), e.g. "17:30"."""

    def __init__(self) -> None:
        super().__init__(max_length=None, error_messages=_same_message(TIME_MESSAGE))

    def to_internal_value(self, data: object) -> time:  # type: ignore[override]
        """Return the time, or refuse with one plain message."""
        if not isinstance(data, str):
            self.fail("invalid")
        found = _TIME.match(data)
        if found is None:
            self.fail("invalid")
        return time(int(found[1]), int(found[2]))


class OptionalDayField(DayField):
    """A calendar day as YYYY-MM-DD, or nothing (null, empty or left out)."""

    def __init__(self) -> None:
        super().__init__()
        self.required = False
        self.allow_null = True

    def run_validation(self, data: object = serializers.empty) -> date | None:
        """Take empty text as no day."""
        if data == "" or data is None:
            return None
        day: date | None = super().run_validation(data)
        return day


class ReasonField(StrictCharField):
    """Why: required, line breaks allowed; by default a correction's reason (300 characters)."""

    def __init__(
        self,
        *,
        max_length: int = CORRECTION_REASON_MAX_LENGTH,
        message: str = REASON_MESSAGE,
    ) -> None:
        super().__init__(
            max_length=max_length,
            error_messages={
                **_same_message(message),
                "max_length": f"Use at most {max_length} characters.",
            },
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse anything but text, and control characters other than line breaks and tabs."""
        if not isinstance(data, str):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if NOTE_CONTROL_CHARACTERS.search(value):
            raise serializers.ValidationError(NOTE_CHARACTERS_MESSAGE)
        return value


class WorkingDayMinutesField(serializers.Field[int, int, int, Any]):
    """The Working Day in minutes: a whole number from 240 to 720, in steps of 30."""

    default_error_messages = _same_message(WORKING_DAY_MESSAGE)  # DRF's own

    def to_internal_value(self, data: object) -> int:
        """Return the minutes, or refuse with one plain message (never a bool, float or text)."""
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail("invalid")
        if not WORKING_DAY_MIN <= data <= WORKING_DAY_MAX or data % WORKING_DAY_STEP:
            self.fail("invalid")
        return data

    def to_representation(self, value: int) -> int:
        """The minutes as stored."""
        return value


class NothingSerializer(StrictSerializer[None]):
    """Clocking in or out takes nothing: the time comes from the server clock."""


class MonthQuerySerializer(StrictSerializer[None]):
    """Which month (this month by default), and whose (one's own by default)."""

    month = MonthField()
    staff = RecordIdField(STAFF_MESSAGE, required=False)

    def year_month(self) -> tuple[int, int]:
        """The month asked for, or this month in the Consultancy's time zone."""
        return _year_month(self.validated_data.get("month"))

    def staff_id(self) -> UUID | None:
        """Whose month was asked for, if anyone's but one's own, by UUID."""
        staff: UUID | None = self.validated_data.get("staff")
        return staff


class TeamMonthQuerySerializer(StrictSerializer[None]):
    """Which month of the team view (this month by default)."""

    month = MonthField()

    def year_month(self) -> tuple[int, int]:
        """The month asked for, or this month in the Consultancy's time zone."""
        return _year_month(self.validated_data.get("month"))


class ClockInExportQuerySerializer(StrictSerializer[None]):
    """The team view's filters for an export: the month is always given."""

    month = MonthField(required=True)

    def year_month(self) -> tuple[int, int]:
        """The month asked for."""
        return _year_month(self.validated_data["month"])


WEEKLY_OFF_MESSAGE = "Choose Saturday, Sunday, Saturday and Sunday, or none."


class WeeklyOffSerializer(StrictSerializer[None]):
    """The weekly days off: one of the four choices, exactly as sent (asked 2026-11-17)."""

    weekly_off = serializers.ChoiceField(
        choices=WeeklyOff.values,
        error_messages=dict.fromkeys(
            ("required", "null", "blank", "invalid", "invalid_choice"), WEEKLY_OFF_MESSAGE
        ),
    )

    def choice(self) -> WeeklyOff:
        """The checked choice."""
        return WeeklyOff(self.validated_data["weekly_off"])


class WorkingDaySerializer(StrictSerializer[None]):
    """A new Working Day, in minutes."""

    minutes = WorkingDayMinutesField()

    def working_day(self) -> int:
        """The checked minutes."""
        minutes: int = self.validated_data["minutes"]
        return minutes


class ClockOutCorrectionSerializer(StrictSerializer[None]):
    """A missing clock-out to set: the time on that day, and why."""

    clocked_out_at = TimeOfDayField()
    reason = ReasonField()

    def clock_out(self) -> time:
        """The checked time of day."""
        at: time = self.validated_data["clocked_out_at"]
        return at

    def reason_text(self) -> str:
        """The checked reason."""
        reason: str = self.validated_data["reason"]
        return reason


class MissedTimeSerializer(StrictSerializer[None]):
    """Missed time to add: the day, from and to (times on that day), and why."""

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """Declared here: `from` can't be a class attribute name."""
        return {
            "day": DayField(),
            "from": TimeOfDayField(),
            "to": TimeOfDayField(),
            "reason": ReasonField(),
        }

    def day(self) -> date:
        """The checked day."""
        day: date = self.validated_data["day"]
        return day

    def span(self) -> tuple[time, time]:
        """The checked start and end."""
        return self.validated_data["from"], self.validated_data["to"]

    def reason_text(self) -> str:
        """The checked reason."""
        reason: str = self.validated_data["reason"]
        return reason


class WorkFromHomeSerializer(StrictSerializer[None]):
    """Work From Home dates asked for or set: from today, within a year, and why."""

    starts_on = DayField()
    ends_on = OptionalDayField()
    reason = ReasonField(
        max_length=WORK_FROM_HOME_REASON_MAX_LENGTH, message=WORK_FROM_HOME_REASON_MESSAGE
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Start from today, within a year; end on or after the start."""
        today = timezone.localdate()
        starts_on: date = attrs["starts_on"]
        ends_on: date | None = attrs.get("ends_on")
        if starts_on < today:
            raise serializers.ValidationError({"starts_on": [START_BEFORE_TODAY_MESSAGE]})
        if starts_on > today + WORK_FROM_HOME_AHEAD:
            raise serializers.ValidationError({"starts_on": [START_TOO_LATE_MESSAGE]})
        if ends_on is not None and ends_on < starts_on:
            raise serializers.ValidationError({"ends_on": [END_BEFORE_START_MESSAGE]})
        return attrs

    def new_dates(self) -> NewDates:
        """The checked dates."""
        data = self.validated_data
        return NewDates(
            starts_on=data["starts_on"], ends_on=data.get("ends_on"), reason=data["reason"]
        )


def _year_month(month: tuple[int, int] | None) -> tuple[int, int]:
    if month is not None:
        return month
    today = timezone.localdate()
    return today.year, today.month


class HolidayNameField(StrictCharField):
    """A holiday's name: one line of up to 60 characters, trimmed (e.g. "Dashain")."""

    def __init__(self) -> None:
        super().__init__(
            max_length=HOLIDAY_NAME_MAX_LENGTH,
            error_messages={
                **_same_message(HOLIDAY_NAME_MESSAGE),
                "max_length": f"Use at most {HOLIDAY_NAME_MAX_LENGTH} characters.",
            },
        )

    def to_internal_value(self, data: object) -> str:
        """Refuse anything but text, and any control character."""
        if not isinstance(data, str):
            self.fail("invalid")
        if _ANY_CONTROL.search(data):
            raise serializers.ValidationError(ONE_LINE_MESSAGE)
        return super().to_internal_value(data)


class HolidayDaysField(serializers.Field[list[date], list[str], list[str], object]):
    """The days of one holiday: 1 to 31 different days, each YYYY-MM-DD, returned in order.

    One plain message per mistake, never a message per item, so nothing sent is repeated back.
    """

    default_error_messages = {  # noqa: RUF012  # DRF's own
        "required": HOLIDAY_DAYS_MESSAGE,
        "null": HOLIDAY_DAYS_MESSAGE,
        "count": HOLIDAY_DAYS_MESSAGE,
        "day": DAY_MESSAGE,
        "repeated": HOLIDAY_DAYS_ONCE_MESSAGE,
    }

    def to_internal_value(self, data: object) -> list[date]:
        """Return the days, or refuse the whole list."""
        if not isinstance(data, list) or not 1 <= len(data) <= HOLIDAY_GROUP_MAX:
            self.fail("count")
        day_field = DayField()
        try:
            days = [day_field.to_internal_value(item) for item in data]
        except serializers.ValidationError:
            # self.fail raises the field's validation error
            self.fail("day")
        if len(set(days)) != len(days):
            self.fail("repeated")
        return sorted(days)

    def to_representation(self, value: list[date]) -> list[str]:
        """The days as text."""
        return [day.isoformat() for day in value]


class HolidayIdsField(serializers.Field[list[UUID], list[str], list[str], object]):
    """The holidays to remove together: 1 to 31 different record ids (ADR 0027)."""

    default_error_messages = {  # noqa: RUF012  # DRF's own
        "required": HOLIDAY_IDS_MESSAGE,
        "null": HOLIDAY_IDS_MESSAGE,
        "count": HOLIDAY_IDS_MESSAGE,
        "id": NOT_A_RECORD_ID_MESSAGE,
        "repeated": HOLIDAY_IDS_ONCE_MESSAGE,
    }

    def to_internal_value(self, data: object) -> list[UUID]:
        """Return the ids, or refuse the whole list."""
        if not isinstance(data, list) or not 1 <= len(data) <= HOLIDAY_GROUP_MAX:
            self.fail("count")
        ids = [parse_record_id(item) for item in data]
        found = [one for one in ids if one is not None]
        if len(found) != len(ids):
            self.fail("id")
        if len(set(found)) != len(found):
            self.fail("repeated")
        return found

    def to_representation(self, value: list[UUID]) -> list[str]:
        """The ids as text."""
        return [str(one) for one in value]


class HolidaySerializer(StrictSerializer[None]):
    """A new holiday: its name and every day of it (UI revamp, ticket 17)."""

    name = HolidayNameField()
    dates = HolidayDaysField()

    def days(self) -> list[date]:
        """The checked days, in order."""
        days: list[date] = self.validated_data["dates"]
        return days

    def holiday_name(self) -> str:
        """The checked, trimmed name."""
        name: str = self.validated_data["name"]
        return name


class HolidayIdsSerializer(StrictSerializer[None]):
    """The holidays to remove together."""

    ids = HolidayIdsField()

    def holiday_ids(self) -> list[UUID]:
        """The checked ids."""
        ids: list[UUID] = self.validated_data["ids"]
        return ids
