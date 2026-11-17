from __future__ import annotations

from typing import Any

from django.db import models
from django.utils import timezone
from django_tenants.models import DomainMixin, TenantMixin

from tenancy.exceptions import StatusRecordIsAppendOnlyError
from tenancy.status import ConsultancyStatus
from tenancy.theme import DEFAULT_THEME, theme_to_stored
from tenancy.types import DateDisplay

CONSULTANCY_NAME_MAX_LENGTH = 200
LOGO_KEY_MAX_LENGTH = 200
LOGO_CONTENT_TYPE_MAX_LENGTH = 40
STATUS_MAX_LENGTH = 20
STATUS_REASON_MAX_LENGTH = 500
_STATUS_CHOICES = [(value.value, value.value) for value in ConsultancyStatus]


class SetupState(models.TextChoices):
    """How far a new Consultancy's setup has come (issue 60).

    Its schema is built by a background job; only a ready one is reachable or listed anywhere
    outside the Platform.
    """

    SETTING_UP = "setting_up", "Setting up"
    READY = "ready", "Ready"
    FAILED = "failed", "Setup failed"


def default_theme() -> dict[str, object]:
    """Return the stored form of the Theme new Consultancies start with."""
    return theme_to_stored(DEFAULT_THEME)


class Consultancy(TenantMixin):  # type: ignore[misc]  # django-tenants is untyped
    """A Consultancy using the product; its data lives in its own schema."""

    name = models.CharField(max_length=CONSULTANCY_NAME_MAX_LENGTH)
    # The Consultancy Status (spec 13-13d), changed only through tenancy.services; the access
    # rules are in tenancy.status. No data is ever deleted, whatever the Status.
    status = models.CharField(
        max_length=STATUS_MAX_LENGTH,
        choices=_STATUS_CHOICES,
        default=ConsultancyStatus.EVALUATION.value,
        db_index=True,
    )
    status_reason = models.CharField(max_length=STATUS_REASON_MAX_LENGTH, blank=True, default="")
    # Evaluation: everything works until this moment, then nothing (no end date counts as ended).
    evaluation_ends_at = models.DateTimeField(null=True, blank=True)
    # Active: the day the office has paid up to (for the Platform list; nothing is blocked on it).
    paid_until = models.DateField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # The Theme's stored form; read it through tenancy.services.get_theme.
    theme = models.JSONField(default=default_theme)
    # The switchable Modules that are on, as a list of their values; read it through
    # modules.services. Switching one off never touches its data.
    switched_on_modules = models.JSONField(default=list)
    # The calendar dates are shown and typed in (Configuration → Dates); stored dates are always
    # AD. Read it through tenancy.services.get_date_display.
    date_display = models.CharField(
        max_length=2, choices=[(value, value) for value in DateDisplay], default=DateDisplay.AD
    )
    # The office logo (Configuration → Office logo, spec 99c), kept in private storage under this
    # Consultancy's prefix. No key means no logo: the initials show. The version grows with every
    # upload, so each logo has its own address. Read it through tenancy.services.get_logo.
    logo_key = models.CharField(max_length=LOGO_KEY_MAX_LENGTH, blank=True, default="")
    logo_content_type = models.CharField(
        max_length=LOGO_CONTENT_TYPE_MAX_LENGTH, blank=True, default=""
    )
    logo_version = models.PositiveIntegerField(default=0)
    show_name_beside_logo = models.BooleanField(default=True)
    # Setting up until the background job has built its schema and invited its first Admin
    # (issue 60). Offices made before the job existed are ready.
    setup_state = models.CharField(
        max_length=STATUS_MAX_LENGTH,
        choices=SetupState.choices,
        default=SetupState.READY,
        db_index=True,
    )

    # The schema is built on purpose (tenancy.services), never as a side effect of saving.
    auto_create_schema = False

    class Meta:
        verbose_name_plural = "consultancies"

    def __str__(self) -> str:
        return str(self.name)


class Domain(DomainMixin):  # type: ignore[misc]  # django-tenants is untyped
    """A host name (subdomain) that resolves to a Consultancy, unless it is switched off."""

    # Terminating a Consultancy switches off every one of its hosts: they answer 404.
    is_active = models.BooleanField(default=True)


class AppendOnlyQuerySet(models.QuerySet["ConsultancyStatusChange"]):
    """Refuses bulk changes and bulk deletes."""

    def update(self, **kwargs: Any) -> int:  # noqa: ANN401  # Django's own signature
        """Refuse: the record is never changed."""
        raise StatusRecordIsAppendOnlyError

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse: the record is never deleted."""
        raise StatusRecordIsAppendOnlyError


class ConsultancyStatusChange(models.Model):
    """One change of a Consultancy's Status, kept on the Platform side for good (spec 13)."""

    consultancy = models.ForeignKey(
        Consultancy, on_delete=models.PROTECT, related_name="status_changes"
    )
    # The Platform Admin who made it; None only for changes made by a migration.
    changed_by = models.ForeignKey(
        "platform_admin.PlatformAdmin", on_delete=models.PROTECT, null=True, related_name="+"
    )
    # Empty for a new Consultancy's starting Status.
    from_status = models.CharField(max_length=STATUS_MAX_LENGTH, blank=True, default="")
    to_status = models.CharField(max_length=STATUS_MAX_LENGTH, choices=_STATUS_CHOICES)
    reason = models.CharField(max_length=STATUS_REASON_MAX_LENGTH, blank=True, default="")
    # The dates the change set, if any.
    evaluation_ends_at = models.DateTimeField(null=True, blank=True)
    paid_until = models.DateField(null=True, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ("-changed_at", "-pk")
        indexes = (models.Index(fields=("consultancy", "-changed_at")),)

    def __str__(self) -> str:
        return f"{self.from_status or 'new'} -> {self.to_status}"

    def save(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401  # Django's own signature
        """Add a new entry; refuse to change an existing one."""
        if self.pk is not None:
            raise StatusRecordIsAppendOnlyError
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:  # noqa: ANN401
        """Refuse: the record is never deleted."""
        raise StatusRecordIsAppendOnlyError
