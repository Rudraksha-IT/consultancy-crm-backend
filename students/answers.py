"""Students as the API returns them. Built by hand so nothing extra ever leaks out."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from django.utils import timezone

from access.policy import StudentAction, can_act_on_student, why_cannot_ask_transfer
from applications.answers import application_answer, visa_answer
from documents.answers import progress_answer
from documents.services import checklist_of
from history.answers import history_entry_answer
from history.services import stage_changes_of
from home.services import progress_of_student
from staff.models import Staff
from students.models import AcademicRecord, Student, TestScore, WorkExperience
from students.passport import passport_warning
from students.profile import ADDRESS_PARTS
from tasks.services import open_tasks_of
from transfer_requests.answers import transfer_request_answer
from transfer_requests.services import waiting_request_for


def _counsellor(staff: Staff | None) -> dict[str, object] | None:
    return None if staff is None else {"id": staff.public_id, "name": staff.name}


def student_row(student: Student) -> dict[str, object]:
    """One Student as the Student list shows them."""
    return {
        "id": student.public_id,
        "name": student.name,
        "phone": student.phone,
        "email": student.email,
        "country": student.country,
        "source": student.source,
        "stage": student.stage,
        "assigned_counsellor": _counsellor(student.assigned_counsellor),
    }


def student_answer(student: Student) -> dict[str, object]:
    """One Student with every detail the new-visitor form gave."""
    return {
        **student_row(student),
        "highest_qualification": student.highest_qualification,
        "past_degree": student.past_degree,
        "previous_job": student.previous_job,
        "sub_agent_name": student.sub_agent_name,
        "note": student.note,
        "created_at": student.created_at.isoformat(),
    }


def academic_record_answer(record: AcademicRecord) -> dict[str, object]:
    """One qualification in a Student's academic history."""
    return {
        "id": record.public_id,
        "level": record.level,
        "institution": record.institution,
        "finished_year": record.finished_year,
        "result": record.result,
    }


def test_score_answer(score: TestScore) -> dict[str, object]:
    """One English test result, the score as text with one decimal ("6.5")."""
    return {
        "id": score.public_id,
        "test": score.test,
        "overall": f"{Decimal(score.overall):.1f}",
        "taken_on": None if score.taken_on is None else score.taken_on.isoformat(),
        "details": score.details,
    }


def work_experience_answer(job: WorkExperience) -> dict[str, object]:
    """One job, its months as "YYYY-MM"; no end month means they still work there."""
    return {
        "id": job.public_id,
        "employer": job.employer,
        "job_title": job.job_title,
        "started_on": _month(job.started_on),
        "ended_on": None if job.ended_on is None else _month(job.ended_on),
    }


def _chosen_offer_id(student: Student) -> UUID | None:
    """The chosen Application's UUID, from the Applications already loaded with the Student."""
    if student.chosen_offer_id is None:
        return None
    chosen = next(a for a in student.applications.all() if a.pk == student.chosen_offer_id)
    return chosen.public_id


def _month(day: date) -> str:
    return day.strftime("%Y-%m")


def _iso(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


def _address(student: Student, kind: str) -> dict[str, object]:
    return {part: getattr(student, f"{kind}_{part}") for part in ADDRESS_PARTS}


def personal_details(student: Student) -> dict[str, object]:
    """Personal, family, passport and address details (spec 35a-35e). Never for a Teacher."""
    warning = passport_warning(student.passport_expiry_date, today=timezone.localdate())
    return {
        "date_of_birth": _iso(student.date_of_birth),
        "gender": student.gender,
        "marital_status": student.marital_status,
        "nationality": student.nationality,
        "father_name": student.father_name,
        "mother_name": student.mother_name,
        "guardian_name": student.guardian_name,
        "guardian_relation": student.guardian_relation,
        "guardian_phone": student.guardian_phone,
        "passport_number": student.passport_number,
        "passport_place_of_issue": student.passport_place_of_issue,
        "passport_issue_date": _iso(student.passport_issue_date),
        "passport_expiry_date": _iso(student.passport_expiry_date),
        "passport_warning": None if warning is None else warning.value,
        "permanent_address": _address(student, "permanent"),
        "mailing_address": _address(student, "mailing"),
        "mailing_same_as_permanent": student.mailing_same_as_permanent,
        "target_level": student.target_level,
    }


def student_profile(student: Student, viewer: Staff) -> dict[str, object]:
    """One Student as their profile shows them, and what the viewer may do with them."""

    def may(action: StudentAction) -> bool:
        return can_act_on_student(viewer, action, student)

    return {
        **student_answer(student),
        "other_countries": list(student.other_countries),
        **personal_details(student),
        "preferred_course": student.preferred_course,
        "preferred_intake": student.preferred_intake,
        "academic_records": [academic_record_answer(r) for r in student.academic_records.all()],
        "test_scores": [test_score_answer(score) for score in student.test_scores.all()],
        "work_experience": [work_experience_answer(job) for job in student.work_experience.all()],
        "lost_reason": student.lost_reason,
        # Every Stage change, oldest first: the timeline works out time spent from these (issue 82).
        "stage_changes": [history_entry_answer(e) for e in stage_changes_of(student)],
        "progress": _progress_answer(student),
        "applications": [application_answer(a) for a in student.applications.all()],
        "chosen_offer_id": _chosen_offer_id(student),
        "visas": [visa_answer(visa) for visa in student.visas.all()],
        "documents": progress_answer(checklist_of(student).checklist),
        "details_version": student.details_version,
        "can_change": may(StudentAction.CHANGE),
        "can_edit_details": may(StudentAction.EDIT_DETAILS),
        "can_set_stage": may(StudentAction.SET_STAGE),
        "can_assign": may(StudentAction.ASSIGN),
        "can_transfer": may(StudentAction.TRANSFER),
        "can_change_applications": may(StudentAction.CHANGE_APPLICATIONS),
        "can_change_documents": may(StudentAction.CHANGE_DOCUMENTS),
        "can_record_payment": may(StudentAction.RECORD_PAYMENT),
        "can_change_fees": may(StudentAction.CHANGE_FEES),
        "open_tasks": open_tasks_of(student).count(),
        **_transfer_request_parts(student, viewer),
    }


def _progress_answer(student: Student) -> dict[str, object]:
    """How far along they are and their next step, by the same rule as Home (spec 113)."""
    progress = progress_of_student(student)
    return {
        "percent": progress.percent,
        "next_step": progress.next_step,
        "is_done": progress.is_done,
    }


def _transfer_request_parts(student: Student, viewer: Staff) -> dict[str, object]:
    """The Transfer Request waiting for the Student, if any, and whether the viewer may ask.

    The viewer may ask when the policy lets them and nobody is waiting already (spec 44a, 44e).
    """
    waiting = waiting_request_for(student)
    may_ask = waiting is None and why_cannot_ask_transfer(viewer, student) is None
    return {
        "can_ask_transfer": may_ask,
        "transfer_request": None if waiting is None else transfer_request_answer(waiting, viewer),
    }
