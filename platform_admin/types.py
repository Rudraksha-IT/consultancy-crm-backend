from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from modules.types import Module
from tenancy.models import SetupState
from tenancy.status import ConsultancyStatus
from tenancy.theme import ThemeConfig


@dataclass(frozen=True)
class PlatformAdminDetails:
    """A Platform Admin's name and email, as given; `save_platform_admin` checks them."""

    name: str
    email: str


@dataclass(frozen=True)
class ConsultancyOnboarding:
    """What a Platform Admin enters to start a new Consultancy."""

    name: str
    subdomain: str
    theme: ThemeConfig
    # Where replies to the office's emails go (ticket 20); checked, never empty.
    contact_email: str
    # The starting Status: Evaluation (14 days), or Active paid up to `paid_until` (spec 13a).
    status: ConsultancyStatus = ConsultancyStatus.EVALUATION
    paid_until: date | None = None


@dataclass(frozen=True)
class ConsultancyOverview:
    """One row of the Platform's Consultancy list."""

    id: UUID
    name: str
    subdomain: str
    host: str
    status: ConsultancyStatus
    status_reason: str
    evaluation_ends_at: datetime | None
    paid_until: date | None
    modules: list[Module]
    theme: ThemeConfig
    # Setting up, ready or failed (issue 60); only a ready one can be opened or changed.
    setup_state: SetupState
