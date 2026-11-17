from __future__ import annotations

from dataclasses import dataclass

from staff.models import Staff
from students.models import Country, Qualification, Source, Stage, TargetLevel


@dataclass(frozen=True)
class NewStudent:
    """What the new-visitor form gives about a Lead (spec 23)."""

    name: str
    phone: str
    country: Country
    source: Source
    highest_qualification: Qualification
    email: str = ""
    past_degree: str = ""
    previous_job: str = ""
    sub_agent_name: str = ""
    note: str = ""
    assigned_counsellor: Staff | None = None


# Who a Student is assigned to: "me", "none" (no Counsellor yet), or a Staff id.
COUNSELLOR_ME = "me"
COUNSELLOR_NONE = "none"


@dataclass(frozen=True)
class StudentFilters:
    """Search and filters for the Student list (spec 29-31). Empty means "any"."""

    query: str = ""
    stage: Stage | None = None
    source: Source | None = None
    counsellor: str = ""
    # The country they most want to study in, and the level they aim for (spec 31).
    country: Country | None = None
    target_level: TargetLevel | None = None
