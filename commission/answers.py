"""Commission as the API returns it. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from commission.models import Commission
from commission.services import CommissionTotals


def commission_answer(commission: Commission) -> dict[str, object]:
    """One Commission: whose, which University and Intake, how much, and where it is."""
    student, university = commission.student, commission.university
    return {
        "id": commission.public_id,
        "student": {"id": student.public_id, "name": student.name},
        "university": {"id": university.public_id, "name": university.name},
        "intake": commission.intake,
        "amount": commission.amount,
        "status": commission.status,
        "received_on": None
        if commission.received_on is None
        else commission.received_on.isoformat(),
    }


def totals_answer(totals: CommissionTotals) -> dict[str, int]:
    """What all Commission comes to by status, and what is still to come."""
    return {
        "waiting_for_enrolment": totals.waiting_for_enrolment,
        "claim_sent": totals.claim_sent,
        "received": totals.received,
        "to_come": totals.to_come,
    }
