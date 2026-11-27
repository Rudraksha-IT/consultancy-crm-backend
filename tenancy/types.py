from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from tenancy.status import ConsultancyStatus


class DateDisplay(StrEnum):
    """The calendar a Consultancy's dates are shown in; stored dates are always AD."""

    AD = "AD"
    BS = "BS"


@dataclass(frozen=True)
class OfficeLogo:
    """The office logo as the frontend gets it; the name always shows beside it."""

    url: str


@dataclass(frozen=True)
class StoredLogo:
    """The office logo's bytes and the type they are served as."""

    content: bytes
    content_type: str


@dataclass(frozen=True)
class ConsultancyAccess:
    """The Consultancy Status as Staff see it (spec 13d).

    `message` says why Staff are limited, or is None; `evaluation_ends_at` is set only in
    Evaluation, so the screens can warn before it ends.
    """

    status: ConsultancyStatus
    message: str | None
    evaluation_ends_at: datetime | None


@dataclass(frozen=True)
class StatusChange:
    """A Platform Admin's change of a Consultancy's Status, with the date it needs.

    Evaluation means extending it to `evaluation_ends_at`; Active needs `paid_until`.
    """

    status: ConsultancyStatus
    reason: str
    paid_until: date | None = None
    evaluation_ends_at: datetime | None = None
