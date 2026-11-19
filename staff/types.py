from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from staff.models import ColourMode, Role

if TYPE_CHECKING:
    from tenancy.models import Consultancy


@dataclass(frozen=True)
class NewStaff:
    """The details needed to add a Staff member.

    The first Admin, made by a Platform Admin, may have no mobile or job title yet; no joining
    date means today.
    """

    name: str
    email: str
    role: Role
    mobile: str = ""
    job_title: str = ""
    joined_on: date | None = None


@dataclass(frozen=True)
class Office:
    """Where an email about joining comes from: the office (for its look) and its sign-up page."""

    consultancy: Consultancy
    join_link: str


@dataclass(frozen=True)
class NotificationChoices:
    """Which emails the app sends a Staff member (spec 22f)."""

    task_given: bool
    task_due_tomorrow: bool
    morning_summary: bool


@dataclass(frozen=True)
class MyDetailsChange:
    """What a Staff member changes about themselves; None leaves a value as it is.

    Only these: job title, Role, work email and joining date are set by an Admin.
    """

    name: str | None = None
    mobile: str | None = None
    colour_mode: ColourMode | None = None
    page_swipe: bool | None = None
    task_given: bool | None = None
    task_due_tomorrow: bool | None = None
    morning_summary: bool | None = None
