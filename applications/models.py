from __future__ import annotations

from django.db import models
from django.utils import timezone

from applications.appointments import VisaDates
from applications.statuses import ApplicationStatus, OfferKind, VisaStatus
from catalogue.models import Course
from staff.models import Staff
from students.models import CHOICE_MAX_LENGTH, INTAKE_LENGTH, Student


class Application(models.Model):
    """One Student's application to one Course at one University for one Intake (spec 52).

    The Intake is kept as its month ("YYYY-MM", AD), so an Intake later removed from the Course
    stays on the Applications that used it. Courses are archived, never deleted (PROTECT).
    """

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="applications")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="applications")
    intake = models.CharField(max_length=INTAKE_LENGTH)
    status = models.CharField(
        max_length=CHOICE_MAX_LENGTH,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.APPLIED,
    )
    # Conditional or Unconditional, while it is at Offer; empty when not known (issue 74).
    offer_kind = models.CharField(
        max_length=CHOICE_MAX_LENGTH, choices=OfferKind.choices, blank=True
    )
    # The day (Kathmandu) it first became Applied; set once by the service, never changed after.
    # Empty for Applications sent before it was recorded (spec 57b).
    applied_on = models.DateField(null=True, blank=True)
    # The day (Kathmandu) its Offer Letter was added; empty before it has one, or when it was
    # converted from an older Status (issue 76).
    offered_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("pk",)
        constraints = (
            models.UniqueConstraint(
                "student", "course", "intake", name="applications_one_per_course_and_intake"
            ),
        )

    def __str__(self) -> str:
        return f"{self.course_id} {self.intake}"


class Visa(models.Model):
    """One visa application, added once it is sent to the embassy (spec 58-62; issue 75).

    Only for the Student's Chosen Offer (issue 76). A Student keeps every attempt, so a
    refused-then-granted Student shows both. Newest first.
    """

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="visas")
    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name="visas")
    status = models.CharField(
        max_length=CHOICE_MAX_LENGTH,
        choices=VisaStatus.choices,
        default=VisaStatus.APPLIED,
    )
    # The day (Kathmandu) it was applied for, shown as "Applied <day>".
    started_on = models.DateField(default=timezone.localdate)
    # The day (Kathmandu) it last became Granted; set by the service, for the Reports (issue 53).
    granted_on = models.DateField(null=True, blank=True)
    # Appointments, if booked (spec 62a). Stored in UTC, shown in Kathmandu time.
    biometrics_at = models.DateTimeField(null=True, blank=True)
    medical_at = models.DateTimeField(null=True, blank=True)
    interview_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(Staff, on_delete=models.PROTECT, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-pk",)

    def __str__(self) -> str:
        return f"{self.application_id} {self.status}"

    @property
    def dates(self) -> VisaDates:
        """The appointments booked on this Visa."""
        return VisaDates(
            biometrics=self.biometrics_at, medical=self.medical_at, interview=self.interview_at
        )
