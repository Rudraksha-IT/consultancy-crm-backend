from __future__ import annotations

from django.db import models

from codes.types import PURPOSE_MAX_LENGTH, CodePurpose
from staff.models import Staff

# hex HMAC-SHA256 and a 16-byte hex salt.
CODE_HASH_LENGTH = 64
SALT_LENGTH = 32


class CodeRecord(models.Model):
    """What every Code keeps, whoever it belongs to. Only its keyed hash, never the Code itself.

    Rows are kept after use or replacement so the hourly limit can count them.
    """

    purpose = models.CharField(max_length=PURPOSE_MAX_LENGTH, choices=CodePurpose.choices)
    salt = models.CharField(max_length=SALT_LENGTH)
    code_hash = models.CharField(max_length=CODE_HASH_LENGTH)
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    replaced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class OneTimeCode(CodeRecord):
    """A Staff member's Code (the office scope).

    It lives in the Consultancy's own schema, so it can only ever work on that Consultancy.
    A Platform Admin's Code is `platform_admin.models.PlatformAdminCode` (issue 63).
    """

    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="one_time_codes")

    class Meta:
        indexes = (models.Index(fields=("staff", "purpose", "created_at")),)

    def __str__(self) -> str:
        return f"{self.purpose} code for staff {self.staff_id}"
