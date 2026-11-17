"""Two-step login records, kept in the schema of the person they belong to (issue 61)."""

from __future__ import annotations

from django.db import models

from auth_tokens.models import KIND_MAX_LENGTH, TokenKind
from staff.models import STAFF_NAME_MAX_LENGTH

# sha256 in hex.
HASH_LENGTH = 64
HOST_MAX_LENGTH = 255
EVENT_MAX_LENGTH = 30
STEP_MAX_LENGTH = 10


class TwoFactorAccount(models.Model):
    """One person's authenticator app: the sealed secret and the last number step used.

    No foreign key: Staff live in each Consultancy's schema, Platform Admins in public.
    """

    kind = models.CharField(max_length=KIND_MAX_LENGTH, choices=TokenKind.choices)
    person_id = models.PositiveBigIntegerField()
    # Sealed with TWO_FACTOR_SECRETS_KEY (two_factor.sealing); empty until set up.
    secret = models.TextField(blank=True)
    # The secret shown during setup, sealed, until a number from it is confirmed.
    pending_secret = models.TextField(blank=True)
    # The newest 30-second step accepted, so a number already seen can't be used again.
    last_step = models.BigIntegerField(null=True, blank=True)
    set_up_at = models.DateTimeField(null=True, blank=True)
    # Wrong numbers counted across all challenges in one window, before a wait.
    wrong_numbers = models.PositiveSmallIntegerField(default=0)
    wrong_window_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("kind", "person_id"), name="two_factor_one_per_person"),
        )

    def __str__(self) -> str:
        return f"Two-step login {self.kind} {self.person_id}"


class RecoveryCode(models.Model):
    """One single-use recovery code, kept as a hash only."""

    account = models.ForeignKey(
        TwoFactorAccount, on_delete=models.CASCADE, related_name="recovery_codes"
    )
    code_hash = models.CharField(max_length=HASH_LENGTH)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("account", "code_hash"), name="recovery_code_unique"),
        )

    def __str__(self) -> str:
        return f"Recovery code {self.pk}"


class ChallengeStep(models.TextChoices):
    """What the person must do after the password: set the app up, or enter a number."""

    SETUP = "setup", "Set up"
    VERIFY = "verify", "Verify"


class TwoFactorChallenge(models.Model):
    """The step between the password and the number: short-lived, single use, on one host.

    The person holds a signed token naming it; only the hash of its random part is kept.
    """

    key_hash = models.CharField(max_length=HASH_LENGTH, unique=True)
    kind = models.CharField(max_length=KIND_MAX_LENGTH, choices=TokenKind.choices)
    person_id = models.PositiveBigIntegerField()
    host = models.CharField(max_length=HOST_MAX_LENGTH)
    step = models.CharField(max_length=STEP_MAX_LENGTH, choices=ChallengeStep.choices)
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    # Set once the challenge is used up: passed, ended by wrong tries, or replaced.
    ended_at = models.DateTimeField(null=True, blank=True)
    wrong_numbers = models.PositiveSmallIntegerField(default=0)
    # Setup shows the secret once per challenge.
    setup_given = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Two-step challenge {self.pk}"


class EventKind(models.TextChoices):
    """What happened to someone's two-step login."""

    SWITCHED_ON = "switched_on", "Switched on"
    SWITCHED_OFF = "switched_off", "Switched off"
    SET_UP = "set_up", "Set up"
    RECOVERY_CODE_USED = "recovery_code_used", "Recovery code used"
    NEW_RECOVERY_CODES = "new_recovery_codes", "New recovery codes"
    # An Admin removed the app and recovery codes of someone who lost their phone (issue 62).
    RESET = "reset", "Reset"


class TwoFactorEvent(models.Model):
    """An append-only record of one change to someone's two-step login (who, what, when).

    In an office it is the Consultancy's record of its Staff's two-step login (issue 62), naming
    who did it as History does: a Platform Admin under their own name, marked, never as Staff.
    """

    kind = models.CharField(max_length=KIND_MAX_LENGTH, choices=TokenKind.choices)
    person_id = models.PositiveBigIntegerField()
    what = models.CharField(max_length=EVENT_MAX_LENGTH, choices=EventKind.choices)
    at = models.DateTimeField(auto_now_add=True)
    # Who did it: the Staff member (themselves or their Admin), or empty for a Platform Admin.
    by_staff_id = models.PositiveBigIntegerField(null=True, blank=True)
    # Their name as it was then, e.g. "Ramesh (Platform Admin)".
    by_name = models.CharField(max_length=STAFF_NAME_MAX_LENGTH, blank=True, default="")
    by_platform_admin = models.BooleanField(default=False)

    class Meta:
        ordering = ("pk",)
        indexes = (models.Index(fields=("kind", "person_id"), name="two_factor_event_person_idx"),)

    def __str__(self) -> str:
        return f"{self.what} {self.kind} {self.person_id}"
