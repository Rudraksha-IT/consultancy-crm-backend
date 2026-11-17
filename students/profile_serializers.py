"""What a Student's profile endpoints accept (spec 35-37). The server never trusts the browser."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import TextChoices
from django.utils import timezone
from rest_framework import serializers

from catalogue.upcoming import upcoming_intakes
from config.serializers import StrictSerializer
from staff.serializers import MobileField, PlainTextField
from students.models import (
    DETAIL_MAX_LENGTH,
    DISTRICT_MAX_LENGTH,
    EMAIL_MAX_LENGTH,
    JOB_MAX_LENGTH,
    MUNICIPALITY_MAX_LENGTH,
    NATIONALITY_MAX_LENGTH,
    OTHER_COUNTRIES_MAX,
    PASSPORT_MAX_LENGTH,
    PLACE_OF_ISSUE_MAX_LENGTH,
    RELATION_MAX_LENGTH,
    RESULT_MAX_LENGTH,
    STREET_MAX_LENGTH,
    STUDENT_NAME_MAX_LENGTH,
    WARD_MAX,
    WARD_MIN,
    Country,
    EnglishTest,
    Gender,
    MaritalStatus,
    Province,
    Qualification,
    Source,
    Student,
    TargetLevel,
    TestScore,
    WorkExperience,
)
from students.serializers import (
    CHOOSE_FROM_LIST_MESSAGE,
    ENTER_NAME_MESSAGE,
    ENTER_PHONE_MESSAGE,
    SUB_AGENT_NEEDED_MESSAGE,
    SUB_AGENT_NOT_NEEDED_MESSAGE,
    NoteField,
    StrictChoiceField,
)

NOTHING_TO_CHANGE_MESSAGE = "Send at least one detail to change."
PASSPORT_PATTERN = re.compile(r"^[A-Z0-9]{5,20}\Z")
PASSPORT_MESSAGE = "Enter 5 to 20 letters and numbers, without spaces."
PREFERRED_INTAKE_MESSAGE = "Choose one of the Catalogue's intakes, or Not decided."
INTAKE_PATTERN = re.compile(r"^20[0-9]{2}-(?:0[1-9]|1[0-2])\Z")
INTAKE_MESSAGE = "Choose a month and year from 2000 on."
OTHER_COUNTRY_TWICE_MESSAGE = "Choose each country only once."
OTHER_COUNTRY_IS_MAIN_MESSAGE = "This is already the country they most want."
# How old a Student can be (spec 35a): a test-prep child of 10 up to a mature Student of 100.
YOUNGEST_AGE = 10
OLDEST_AGE = 100
BIRTH_DATE_MESSAGE = "Enter a date of birth for someone aged 10 to 100."

EXPIRY_AFTER_ISSUE_MESSAGE = "The expiry date must be after the issue date."
WHOLE_ADDRESS_MESSAGE = "Send every part of the address, empty if unknown."
WARD_MESSAGE = "A ward number is a whole number from 1 to 35."
MONTH_PATTERN = re.compile(r"^(?:19[5-9][0-9]|2[0-9]{3})-(?:0[1-9]|1[0-2])\Z")
MONTH_MESSAGE = "Choose a month and year from 1950 on."
MONTH_IN_FUTURE_MESSAGE = "This month can't be in the future."
END_BEFORE_START_MESSAGE = "The end can't be before the start."
NOT_IN_FUTURE_MESSAGE = "This date can't be in the future."
WHOLE_NUMBER_MESSAGE = "Enter a whole number."
EARLIEST_FINISHED_YEAR = 1950
# A course can be finished a few years from now, e.g. a Bachelor still under way.
FINISHED_YEARS_AHEAD = 6
FINISHED_YEAR_MESSAGE = "Enter a year from 1950 to six years from now."
SCORE_ABOVE_TOP_MESSAGE = "{test} scores go up to {top}."
# The highest overall score of each test.
TOP_SCORES: dict[str, Decimal] = {
    EnglishTest.IELTS: Decimal(9),
    EnglishTest.PTE: Decimal(90),
    EnglishTest.TOEFL: Decimal(120),
    EnglishTest.DUOLINGO: Decimal(160),
}


def _optional_text(max_length: int = DETAIL_MAX_LENGTH) -> PlainTextField:
    return PlainTextField(max_length=max_length, required=False, allow_blank=True)


def _optional_choice(choices: type[TextChoices]) -> StrictChoiceField:
    """One of `choices`, or "" for none. A refusal never repeats what was sent."""
    return StrictChoiceField(
        choices=choices.choices,
        required=False,
        allow_blank=True,
        error_messages={"invalid_choice": CHOOSE_FROM_LIST_MESSAGE},
    )


def years_before(day: date, years: int) -> date:
    """The same day `years` earlier; 29 February becomes 28 February in a common year."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        # silent: 29 February in a year without one becomes the 28th
        return day.replace(year=day.year - years, day=28)


class OptionalMobileField(MobileField):
    """A mobile number, or "" for none."""

    def __init__(self) -> None:
        super().__init__()
        self.allow_blank = True

    def to_internal_value(self, data: object) -> str:
        """Return "" as it is; check anything else as a mobile number."""
        if data == "":
            return ""
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    """JSON true or false only: DRF would take "yes", "1" or 1 as booleans."""

    def to_internal_value(self, data: object) -> bool:
        """Refuse anything that isn't already a boolean."""
        if not isinstance(data, bool):
            self.fail("invalid")
        return data


class WardField(serializers.Field[int | None, int | None, int | None, Any]):
    """A ward number from 1 to 35 as a JSON whole number, or null for none."""

    def __init__(self) -> None:
        super().__init__(allow_null=True)

    def to_internal_value(self, data: object) -> int | None:
        """Refuse anything but a whole number in range."""
        if isinstance(data, bool) or not isinstance(data, int) or not WARD_MIN <= data <= WARD_MAX:
            raise serializers.ValidationError(WARD_MESSAGE)
        return data

    def to_representation(self, value: int | None) -> int | None:
        """Return the number (unused: answers are built by hand)."""
        return value


class MonthField(serializers.Field[date, str, str, Any]):
    """A month as "YYYY-MM", from 1950 to this month, stored as its first day."""

    def to_internal_value(self, data: object) -> date:
        """Return the month's first day."""
        if not isinstance(data, str) or not MONTH_PATTERN.match(data):
            raise serializers.ValidationError(MONTH_MESSAGE)
        first = date(int(data[:4]), int(data[5:]), 1)
        if first > timezone.localdate():
            raise serializers.ValidationError(MONTH_IN_FUTURE_MESSAGE)
        return first

    def to_representation(self, value: date) -> str:
        """Return "YYYY-MM"."""
        return value.strftime("%Y-%m")


class AddressSerializer(StrictSerializer[None]):
    """One address in Nepal's format (spec 35d), sent whole: every part, "" or null for none."""

    province = StrictChoiceField(
        choices=Province.choices,
        allow_blank=True,
        error_messages={"invalid_choice": CHOOSE_FROM_LIST_MESSAGE},
    )
    district = PlainTextField(max_length=DISTRICT_MAX_LENGTH, allow_blank=True)
    municipality = PlainTextField(max_length=MUNICIPALITY_MAX_LENGTH, allow_blank=True)
    ward = WardField()
    street = PlainTextField(max_length=STREET_MAX_LENGTH, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Every part is needed, even inside a partial change: an address is replaced whole."""
        parts = list(self.fields.keys())
        missing = [str(part) for part in parts if part not in attrs]
        if missing:
            raise serializers.ValidationError({part: [WHOLE_ADDRESS_MESSAGE] for part in missing})
        return attrs


class StrictIntegerField(serializers.IntegerField):
    """A JSON whole number only: DRF would take "2024" or true as numbers."""

    def to_internal_value(self, data: object) -> int:
        """Refuse anything that isn't already an integer."""
        if isinstance(data, bool) or not isinstance(data, int):
            raise serializers.ValidationError(WHOLE_NUMBER_MESSAGE)
        value: int = super().to_internal_value(data)
        return value


class StudentDetailsSerializer(StrictSerializer[None]):
    """Change some of a Student's details (spec 35). Send it `partial=True` and the Student.

    The Note, Stage and Assigned Counsellor have their own endpoints, so they're refused here.
    """

    name = PlainTextField(
        max_length=STUDENT_NAME_MAX_LENGTH,
        error_messages={"blank": ENTER_NAME_MESSAGE, "required": ENTER_NAME_MESSAGE},
    )
    phone = MobileField(
        error_messages={"blank": ENTER_PHONE_MESSAGE, "required": ENTER_PHONE_MESSAGE}
    )
    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH, allow_blank=True)
    country = StrictChoiceField(choices=Country.choices)
    other_countries = serializers.ListField(
        child=StrictChoiceField(choices=Country.choices), max_length=OTHER_COUNTRIES_MAX
    )
    date_of_birth = serializers.DateField(allow_null=True)
    gender = _optional_choice(Gender)
    marital_status = _optional_choice(MaritalStatus)
    nationality = PlainTextField(max_length=NATIONALITY_MAX_LENGTH, allow_blank=True)
    father_name = _optional_text(STUDENT_NAME_MAX_LENGTH)
    mother_name = _optional_text(STUDENT_NAME_MAX_LENGTH)
    guardian_name = _optional_text(STUDENT_NAME_MAX_LENGTH)
    guardian_relation = _optional_text(RELATION_MAX_LENGTH)
    guardian_phone = OptionalMobileField()
    passport_number = PlainTextField(max_length=PASSPORT_MAX_LENGTH, allow_blank=True)
    passport_place_of_issue = _optional_text(PLACE_OF_ISSUE_MAX_LENGTH)
    passport_issue_date = serializers.DateField(allow_null=True)
    passport_expiry_date = serializers.DateField(allow_null=True)
    permanent_address = AddressSerializer()
    mailing_address = AddressSerializer()
    mailing_same_as_permanent = StrictBooleanField()
    target_level = _optional_choice(TargetLevel)
    highest_qualification = StrictChoiceField(choices=Qualification.choices)
    past_degree = _optional_text()
    previous_job = _optional_text()
    preferred_course = _optional_text()
    preferred_intake = PlainTextField(max_length=DETAIL_MAX_LENGTH, allow_blank=True)
    sub_agent_name = _optional_text()

    def __init__(self, student: Student, data: object) -> None:
        super().__init__(data=data, partial=True)
        self.student = student

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        """Add `source` here: as a class attribute it would hide DRF's own `Field.source`."""
        return {**super().get_fields(), "source": StrictChoiceField(choices=Source.choices)}

    def validate_passport_number(self, value: str) -> str:
        """Upper-case letters and digits only."""
        value = value.upper()
        if value and not PASSPORT_PATTERN.match(value):
            raise serializers.ValidationError(PASSPORT_MESSAGE)
        return value

    def validate_preferred_intake(self, value: str) -> str:
        """Nothing ("Not decided"), the one stored, or one of the Catalogue's upcoming Intakes."""
        if value and not INTAKE_PATTERN.match(value):
            raise serializers.ValidationError(INTAKE_MESSAGE)
        is_kept = value in {"", self.student.preferred_intake}
        if not is_kept and value not in upcoming_intakes(timezone.localdate()):
            raise serializers.ValidationError(PREFERRED_INTAKE_MESSAGE)
        return value

    def validate_date_of_birth(self, value: date | None) -> date | None:
        """Someone aged 10 to 100 today."""
        today = timezone.localdate()
        oldest = years_before(today, OLDEST_AGE + 1) + timedelta(days=1)
        if value is not None and not oldest <= value <= years_before(today, YOUNGEST_AGE):
            raise serializers.ValidationError(BIRTH_DATE_MESSAGE)
        return value

    def validate_passport_issue_date(self, value: date | None) -> date | None:
        """Not in the future."""
        if value is not None and value > timezone.localdate():
            raise serializers.ValidationError(NOT_IN_FUTURE_MESSAGE)
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Check the rules that join fields, against the Student as they will be."""
        if not attrs:
            raise serializers.ValidationError(NOTHING_TO_CHANGE_MESSAGE)
        merged = {field: attrs.get(field, getattr(self.student, field)) for field in _JOINED}
        self._check_other_countries(merged["country"], merged["other_countries"])
        self._check_sub_agent(merged["source"], merged["sub_agent_name"])
        self._check_passport_dates(merged["passport_issue_date"], merged["passport_expiry_date"])
        return attrs

    @staticmethod
    def _check_passport_dates(issued: date | None, expires: date | None) -> None:
        if issued is not None and expires is not None and expires <= issued:
            raise serializers.ValidationError(
                {"passport_expiry_date": [EXPIRY_AFTER_ISSUE_MESSAGE]}
            )

    @staticmethod
    def _check_other_countries(country: str, others: list[str]) -> None:
        if len(set(others)) != len(others):
            raise serializers.ValidationError({"other_countries": [OTHER_COUNTRY_TWICE_MESSAGE]})
        if country in others:
            raise serializers.ValidationError({"other_countries": [OTHER_COUNTRY_IS_MAIN_MESSAGE]})

    @staticmethod
    def _check_sub_agent(source: str, sub_agent_name: str) -> None:
        if source == Source.SUB_AGENT and not sub_agent_name:
            raise serializers.ValidationError({"sub_agent_name": [SUB_AGENT_NEEDED_MESSAGE]})
        if sub_agent_name and source != Source.SUB_AGENT:
            raise serializers.ValidationError({"sub_agent_name": [SUB_AGENT_NOT_NEEDED_MESSAGE]})


_JOINED = (
    "country",
    "other_countries",
    "source",
    "sub_agent_name",
    "passport_issue_date",
    "passport_expiry_date",
)


class NoteSerializer(StrictSerializer[None]):
    """The Student's whole Note, replacing the old text (spec 37)."""

    note = NoteField(required=True)


class AcademicRecordSerializer(StrictSerializer[None]):
    """One qualification in a Student's academic history. PATCH sends `partial=True`."""

    level = StrictChoiceField(choices=Qualification.choices)
    institution = PlainTextField(max_length=DETAIL_MAX_LENGTH)
    finished_year = StrictIntegerField(required=False, allow_null=True)
    result = _optional_text(RESULT_MAX_LENGTH)

    def validate_finished_year(self, value: int | None) -> int | None:
        """From 1950 to a few years from now."""
        latest = timezone.localdate().year + FINISHED_YEARS_AHEAD
        if value is not None and not EARLIEST_FINISHED_YEAR <= value <= latest:
            raise serializers.ValidationError(FINISHED_YEAR_MESSAGE)
        return value


class _TestScoreFields(StrictSerializer[None]):
    """The fields of one English test result, checked against `stored` for a partial change."""

    stored: TestScore | None = None

    test = StrictChoiceField(choices=EnglishTest.choices)
    overall = serializers.DecimalField(max_digits=4, decimal_places=1, min_value=Decimal(0))
    taken_on = serializers.DateField(required=False, allow_null=True)
    details = _optional_text()

    def validate_taken_on(self, value: date | None) -> date | None:
        """Not in the future."""
        if value is not None and value > timezone.localdate():
            raise serializers.ValidationError(NOT_IN_FUTURE_MESSAGE)
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """The overall score is at most the test's top score."""
        test = EnglishTest(attrs.get("test", getattr(self.stored, "test", "")))
        overall: Decimal = attrs.get("overall", getattr(self.stored, "overall", Decimal(0)))
        if overall > TOP_SCORES[test]:
            message = SCORE_ABOVE_TOP_MESSAGE.format(test=test.label, top=TOP_SCORES[test])
            raise serializers.ValidationError({"overall": [message]})
        return attrs


class TestScoreSerializer(_TestScoreFields):
    """One English test result. PATCH sends `partial=True` and the stored score."""

    def __init__(
        self, data: object, stored: TestScore | None = None, *, partial: bool = False
    ) -> None:
        super().__init__(data=data, partial=partial)
        self.stored = stored


class _WorkExperienceFields(StrictSerializer[None]):
    """The fields of one job, checked against `stored` for a partial change."""

    stored: WorkExperience | None = None

    employer = PlainTextField(max_length=JOB_MAX_LENGTH)
    job_title = PlainTextField(max_length=JOB_MAX_LENGTH)
    started_on = MonthField()
    ended_on = MonthField(required=False, allow_null=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """The end month is not before the start month, checked against the stored job too."""
        started: date | None = attrs.get("started_on", getattr(self.stored, "started_on", None))
        ended: date | None = attrs.get("ended_on", getattr(self.stored, "ended_on", None))
        if started is not None and ended is not None and ended < started:
            raise serializers.ValidationError({"ended_on": [END_BEFORE_START_MESSAGE]})
        return attrs


class WorkExperienceSerializer(_WorkExperienceFields):
    """One job a Student has had (spec 35e). PATCH sends `partial=True` and the stored job."""

    def __init__(
        self, data: object, stored: WorkExperience | None = None, *, partial: bool = False
    ) -> None:
        super().__init__(data=data, partial=partial)
        self.stored = stored
