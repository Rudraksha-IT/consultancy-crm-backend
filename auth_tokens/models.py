from __future__ import annotations

from django.db import models

JTI_MAX_LENGTH = 64
# A device id is a random UUID in hex.
DEVICE_ID_MAX_LENGTH = 32
KIND_MAX_LENGTH = 20
# A device is named "browser on system" (issue 71); auth_tokens.browsers makes the name.
UNKNOWN_DEVICE_NAME = "Unknown browser on Unknown system"
# The longest name that can be made ("Samsung Internet on Unknown system"), with room to spare.
MAX_DEVICE_NAME_LENGTH = 60


class TokenKind(models.TextChoices):
    """Who a login token is for, which also decides the hosts it works on."""

    STAFF = "staff", "Staff"
    PLATFORM_ADMIN = "platform_admin", "Platform Admin"
    # A Platform Admin inside one Consultancy during a Support Access visit (issue 27).
    SUPPORT_ACCESS = "support_access", "Support Access"


class IssuedRefreshToken(models.Model):
    """A refresh token handed out at login or refresh (the blacklist of ADR 0003).

    Kept in the schema it was issued on (a Consultancy's, or public for Platform Admins), so it
    can only be looked up there. Revoked by logout, rotation and sign-outs; a revoked or
    unknown refresh token is refused.
    """

    jti = models.CharField(max_length=JTI_MAX_LENGTH, unique=True)
    kind = models.CharField(max_length=KIND_MAX_LENGTH, choices=TokenKind.choices)
    # No foreign key: Staff live in each Consultancy's schema, Platform Admins in public.
    person_id = models.PositiveBigIntegerField()
    # One browser or phone: set at login and kept by every refresh, so the person's Signed-in
    # devices list shows each device once. Never sent to the browser.
    device_id = models.CharField(max_length=DEVICE_ID_MAX_LENGTH)
    # "Chrome on Windows", from the browser's own description at login (issue 71). For people
    # only, never for security.
    device_name = models.CharField(max_length=MAX_DEVICE_NAME_LENGTH, default=UNKNOWN_DEVICE_NAME)
    signed_in_at = models.DateTimeField()
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = (models.Index(fields=["kind", "person_id"], name="auth_tokens_person_idx"),)

    def __str__(self) -> str:
        return f"{self.kind} {self.person_id}"
