from __future__ import annotations

from django.db import models
from django.db.models.functions import Lower

from config.public_ids import public_id_field
from students.models import CHOICE_MAX_LENGTH, INTAKE_LENGTH, Country

UNIVERSITY_NAME_MAX_LENGTH = 200
COURSE_NAME_MAX_LENGTH = 200
# The most Intakes one Course can have: two years of monthly starts.
MAX_INTAKES_PER_COURSE = 24


class CourseLevel(models.TextChoices):
    """How advanced a Course is (spec 47)."""

    BACHELOR = "bachelor", "Bachelor"
    MASTER = "master", "Master"
    DIPLOMA = "diploma", "Diploma"
    GRADUATE_CERTIFICATE = "graduate_certificate", "Graduate Certificate"
    FOUNDATION = "foundation", "Foundation"


class University(models.Model):
    """An institution the Consultancy sends Students to (spec 46). Archived, never deleted."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    name = models.CharField(max_length=UNIVERSITY_NAME_MAX_LENGTH)
    country = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=Country.choices)
    # Archived entries can't be picked for new Applications but stay on old ones (spec 50).
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = (Lower("name"), "pk")
        constraints = (
            models.UniqueConstraint(Lower("name"), name="catalogue_university_unique_name"),
        )

    def __str__(self) -> str:
        return str(self.name)


class Course(models.Model):
    """A study programme at a University (spec 47). Archived, never deleted."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    university = models.ForeignKey(University, on_delete=models.PROTECT, related_name="courses")
    name = models.CharField(max_length=COURSE_NAME_MAX_LENGTH)
    level = models.CharField(max_length=CHOICE_MAX_LENGTH, choices=CourseLevel.choices)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = (Lower("name"), "pk")
        constraints = (
            models.UniqueConstraint(
                "university", Lower("name"), name="catalogue_course_unique_name_per_university"
            ),
        )

    def __str__(self) -> str:
        return str(self.name)


class Intake(models.Model):
    """A start month of a Course, as "YYYY-MM" and always AD (spec 102)."""

    # Its id outside the server (ADR 0027); the number never leaves it.
    public_id = public_id_field()

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="intakes")
    month = models.CharField(max_length=INTAKE_LENGTH)

    class Meta:
        ordering = ("month",)
        constraints = (
            models.UniqueConstraint("course", "month", name="catalogue_intake_unique_month"),
        )

    def __str__(self) -> str:
        return str(self.month)
