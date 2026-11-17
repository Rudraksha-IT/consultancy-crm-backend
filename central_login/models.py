"""The Central login's own tables, in the public schema. Neither holds a raw email."""

from __future__ import annotations

from django.db import models

from tenancy.models import Consultancy

EMAIL_HASH_LENGTH = 64  # HMAC-SHA256 as hex


class StaffDirectoryEntry(models.Model):
    """One office an email belongs to, found by the email's keyed hash (never the email).

    Written only by central_login.directory, which the Staff services call.
    """

    email_hash = models.CharField(max_length=EMAIL_HASH_LENGTH, db_index=True)
    consultancy = models.ForeignKey(Consultancy, on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("email_hash", "consultancy"), name="one_directory_entry_per_office"
            ),
        )

    def __str__(self) -> str:
        return f"Directory entry {self.pk}"


class CentralLoginRequest(models.Model):
    """One email the Central login was asked to send, kept for an hour to limit them."""

    email_hash = models.CharField(max_length=EMAIL_HASH_LENGTH, db_index=True)
    requested_at = models.DateTimeField(db_index=True)

    def __str__(self) -> str:
        return f"Central login request {self.pk}"
