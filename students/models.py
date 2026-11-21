from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models

from config.public_ids import public_id_field
from staff.models import Staff

STUDENT_NAME_MAX_LENGTH = 200
# "+" and 15 digits, the longest international number (E.164).
PHONE_MAX_LENGTH = 16
EMAIL_MAX_LENGTH = 254
CHOICE_MAX_LENGTH = 20
DETAIL_MAX_LENGTH = 200
NOTE_MAX_LENGTH = 5000
LOST_REASON_MAX_LENGTH = 500
PASSPORT_MAX_LENGTH = 20
# An Intake as "YYYY-MM", always AD (spec 102).
INTAKE_LENGTH = 7
RESULT_MAX_LENGTH = 50
# The longest list of countries a Student can also be considering (every other country).
OTHER_COUNTRIES_MAX = 6
NATIONALITY_MAX_LENGTH = 50
RELATION_MAX_LENGTH = 50
PLACE_OF_ISSUE_MAX_LENGTH = 100
DISTRICT_MAX_LENGTH = 60
MUNICIPALITY_MAX_LENGTH = 80
# A street line also holds the old single address line, which was up to 200 characters.
STREET_MAX_LENGTH = DETAIL_MAX_LENGTH
JOB_MAX_LENGTH = 120
# Nepal's wards are numbered from 1; the largest municipality has 35 (spec 35d).
WARD_MIN = 1
WARD_MAX = 35
DEFAULT_NATIONALITY = "Nepali"


class Stage(models.TextChoices):
    """Where a Student is in their journey with the Consultancy (GLOSSARY: Stage)."""

    LEAD = "lead", "Lead"
    COUNSELLING = "counselling", "Counselling"
    APPLYING = "applying", "Applying"
    OFFER = "offer", "Offer"
    VISA = "visa", "Visa"
    LOST = "lost", "Lost"


class Source(models.TextChoices):
    """How a Student first came to the Consultancy (GLOSSARY: Source)."""

    WALK_IN = "walk_in", "Walk-in"
    PHONE_CALL = "phone_call", "Phone call"
    FACEBOOK = "facebook", "Facebook"
    WEBSITE = "website", "Website"
    REFERRAL = "referral", "Referral"
    SUB_AGENT = "sub_agent", "Sub-agent"
    EVENT_FAIR = "event_fair", "Event/Fair"
    OTHER = "other", "Other"


class Country(models.TextChoices):
    """The country a visitor wants to study in."""

    AUSTRALIA = "australia", "Australia"
    UK = "uk", "UK"
    CANADA = "canada", "Canada"
    USA = "usa", "USA"
    NEW_ZEALAND = "new_zealand", "New Zealand"
    JAPAN = "japan", "Japan"
    OTHER = "other", "Other"


class Qualification(models.TextChoices):
    """The highest qualification a visitor has finished."""

    SEE = "see", "SEE"
    PLUS_TWO = "plus_two", "+2"
    DIPLOMA = "diploma", "Diploma"
    BACHELOR = "bachelor", "Bachelor"
    MASTER = "master", "Master"


class Gender(models.TextChoices):
    """A Student's gender (spec 35a)."""

    FEMALE = "female", "Female"
    MALE = "male", "Male"
    OTHER = "other", "Other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say", "Prefer not to say"


class MaritalStatus(models.TextChoices):
    """A Student's marital status (spec 35a)."""

    SINGLE = "single", "Single"
    MARRIED = "married", "Married"
    DIVORCED = "divorced", "Divorced"
    WIDOWED = "widowed", "Widowed"


class TargetLevel(models.TextChoices):
    """The level of study a Student is aiming for (spec 35e)."""

    DIPLOMA = "diploma", "Diploma"
    BACHELOR = "bachelor", "Bachelor"
    MASTER = "master", "Master"
    PHD = "phd", "PhD"


class Province(models.TextChoices):
    """Nepal's seven provinces (spec 35d)."""

    KOSHI = "koshi", "Koshi"
    MADHESH = "madhesh", "Madhesh"
    BAGMATI = "bagmati", "Bagmati"
    GANDAKI = "gandaki", "Gandaki"
    LUMBINI = "lumbini", "Lumbini"
    KARNALI = "karnali", "Karnali"
    SUDURPASHCHIM = "sudurpashchim", "Sudurpashchim"


def _text(max_length: int) -> models.CharField[str, str]:
    return models.CharField(max_length=max_length, blank=True, default="")


def _choice(choices: type[models.TextChoices]) -> models.CharField[str, str]:
    return models.CharField(
        max_length=CHOICE_MAX_LENGTH, choices=choices.choices, blank=True, default=""
    )


def _ward() -> models.PositiveSmallIntegerField[int | None, int | None]:
    return models.PositiveSmallIntegerField(null=True, blank=True)


class Student(models.Model):
    """A person who has engaged with the Consultancy, from first enquiry onward (spec 23)."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    name = models.CharField(max_length=STUDENT_NAME_MAX_LENGTH)
    phone = models.CharField(max_length=PHONE_MAX_LENGTH)
    email = models.EmailField(max_length=EMAIL_MAX_LENGTH, blank=True, default="")
    country = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=Country.choices)
    # Other countries they would also study in, besides `country` (spec 35).
    other_countries = ArrayField(
        models.CharField(max_length=CHOICE_MAX_LENGTH, choices=Country.choices),
        size=OTHER_COUNTRIES_MAX,
        blank=True,
        default=list,
    )
    date_of_birth = models.DateField(null=True, blank=True)
    gender = _choice(Gender)
    marital_status = _choice(MaritalStatus)
    nationality = models.CharField(
        max_length=NATIONALITY_MAX_LENGTH, blank=True, default=DEFAULT_NATIONALITY
    )
    # Family (spec 35b). Personal data: never logged, never shown to a Teacher.
    father_name = _text(STUDENT_NAME_MAX_LENGTH)
    mother_name = _text(STUDENT_NAME_MAX_LENGTH)
    guardian_name = _text(STUDENT_NAME_MAX_LENGTH)
    guardian_relation = _text(RELATION_MAX_LENGTH)
    guardian_phone = _text(PHONE_MAX_LENGTH)
    # Passport (spec 35c). The warning is computed from the expiry date, never stored.
    passport_number = models.CharField(max_length=PASSPORT_MAX_LENGTH, blank=True, default="")
    passport_place_of_issue = _text(PLACE_OF_ISSUE_MAX_LENGTH)
    passport_issue_date = models.DateField(null=True, blank=True)
    passport_expiry_date = models.DateField(null=True, blank=True)
    # Permanent and mailing addresses (spec 35d). "Same as permanent" is a flag, never a copy.
    permanent_province = _choice(Province)
    permanent_district = _text(DISTRICT_MAX_LENGTH)
    permanent_municipality = _text(MUNICIPALITY_MAX_LENGTH)
    permanent_ward = _ward()
    permanent_street = _text(STREET_MAX_LENGTH)
    mailing_same_as_permanent = models.BooleanField(default=True)
    mailing_province = _choice(Province)
    mailing_district = _text(DISTRICT_MAX_LENGTH)
    mailing_municipality = _text(MUNICIPALITY_MAX_LENGTH)
    mailing_ward = _ward()
    mailing_street = _text(STREET_MAX_LENGTH)
    target_level = _choice(TargetLevel)
    preferred_course = models.CharField(max_length=DETAIL_MAX_LENGTH, blank=True, default="")
    preferred_intake = models.CharField(max_length=INTAKE_LENGTH, blank=True, default="")
    highest_qualification = models.CharField(
        max_length=CHOICE_MAX_LENGTH, choices=Qualification.choices
    )
    past_degree = models.CharField(max_length=DETAIL_MAX_LENGTH, blank=True, default="")
    previous_job = models.CharField(max_length=DETAIL_MAX_LENGTH, blank=True, default="")
    source = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=Source.choices)
    # Only for a Student whose Source is Sub-agent.
    sub_agent_name = models.CharField(max_length=DETAIL_MAX_LENGTH, blank=True, default="")
    note = models.TextField(max_length=NOTE_MAX_LENGTH, blank=True, default="")
    stage = models.CharField(
        max_length=CHOICE_MAX_LENGTH, choices=Stage.choices, default=Stage.LEAD
    )
    # Why the Student was lost; only while their Stage is Lost (spec 39). History keeps old ones.
    lost_reason = models.TextField(max_length=LOST_REASON_MAX_LENGTH, blank=True, default="")
    assigned_counsellor = models.ForeignKey(
        Staff,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_students",
    )
    # The one offer the Student goes ahead with (GLOSSARY: Chosen Offer; issue 76). One link, so
    # there can never be two. Set and cleared only by `applications.services`.
    chosen_offer = models.ForeignKey(
        "applications.Application",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    created_by = models.ForeignKey(
        Staff, on_delete=models.PROTECT, null=True, related_name="added_students"
    )
    # Moves on with every change to the details and lists the Edit details page shows, so a save
    # made from an older copy is refused instead of overwriting someone else's (issue 81).
    details_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = (
            models.Index(fields=("stage",)),
            models.Index(fields=("source",)),
            models.Index(fields=("-created_at", "-id")),
        )
        constraints = (
            models.CheckConstraint(
                condition=models.Q(permanent_ward__isnull=True)
                | models.Q(permanent_ward__gte=WARD_MIN, permanent_ward__lte=WARD_MAX),
                name="student_permanent_ward_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(mailing_ward__isnull=True)
                | models.Q(mailing_ward__gte=WARD_MIN, mailing_ward__lte=WARD_MAX),
                name="student_mailing_ward_in_range",
            ),
        )

    def __str__(self) -> str:
        return str(self.name)


class EnglishTest(models.TextChoices):
    """An English test a Student has taken (spec 35)."""

    IELTS = "ielts", "IELTS"
    PTE = "pte", "PTE"
    TOEFL = "toefl", "TOEFL"
    DUOLINGO = "duolingo", "Duolingo"


class AcademicRecord(models.Model):
    """One qualification in a Student's academic history (spec 35)."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="academic_records")
    level = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=Qualification.choices)
    institution = models.CharField(max_length=DETAIL_MAX_LENGTH)
    finished_year = models.PositiveSmallIntegerField(null=True, blank=True)
    # As the Student's marksheet writes it, e.g. "GPA 3.2" or "68%".
    result = models.CharField(max_length=RESULT_MAX_LENGTH, blank=True, default="")

    class Meta:
        ordering = ("pk",)

    def __str__(self) -> str:
        return f"{self.level} at {self.institution}"


class TestScore(models.Model):
    """One English test result of a Student (spec 35)."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="test_scores")
    test = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=EnglishTest.choices)
    overall = models.DecimalField(max_digits=4, decimal_places=1)
    taken_on = models.DateField(null=True, blank=True)
    # The band or section scores, e.g. "L 7, R 6.5, W 6, S 6.5".
    details = models.CharField(max_length=DETAIL_MAX_LENGTH, blank=True, default="")

    class Meta:
        ordering = ("pk",)

    def __str__(self) -> str:
        return f"{self.test} {self.overall}"


class WorkExperience(models.Model):
    """One job a Student has had (spec 35e). No end month means they still work there."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="work_experience")
    employer = models.CharField(max_length=JOB_MAX_LENGTH)
    job_title = models.CharField(max_length=JOB_MAX_LENGTH)
    # The first day of the month they started, and of the month they left.
    started_on = models.DateField()
    ended_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ("-started_on", "-pk")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(ended_on__isnull=True)
                | models.Q(ended_on__gte=models.F("started_on")),
                name="work_experience_ends_after_it_starts",
            ),
        )

    def __str__(self) -> str:
        return f"{self.job_title} at {self.employer}"
