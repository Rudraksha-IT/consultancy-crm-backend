"""Documents as the API returns them. Built by hand so nothing extra (the storage key) leaks."""

from __future__ import annotations

from uuid import UUID

from document_examples.answers import examples_answer
from documents.bin import BinItem
from documents.bin_answers import bin_answer
from documents.checklist import Checklist, ChecklistItem
from documents.models import Document, DocumentFile, DocumentType
from documents.requirements import CountryDocuments, uses_of
from documents.services import Paperwork, StudentChecklist
from staff.models import Staff
from staff.refs import person_ref


def document_type_answer(document_type: DocumentType) -> dict[str, object]:
    """One Document Type, whether every Student needs it, and its usual Document Group."""
    return {
        "id": document_type.public_id,
        "name": document_type.name,
        "needed_by_everyone": document_type.needed_by_everyone,
        "usual_group": document_type.usual_group,
    }


def configured_type_answer(document_type: DocumentType) -> dict[str, object]:
    """One Document Type in Configuration, with where it is used (from `configured_types`)."""
    uses = uses_of(document_type)
    return {
        **document_type_answer(document_type),
        "instruction": document_type.instruction,
        "examples": examples_answer(getattr(document_type, "live_examples", ())),
        # Every example file, counting the Bin: a type with any is never removed.
        "example_count": getattr(document_type, "example_count", 0),
        "used_by": {
            "countries": uses.countries,
            "courses": uses.courses,
            "students": uses.students,
            "documents": uses.documents,
        },
    }


def country_list_answer(one: CountryDocuments) -> dict[str, object]:
    """One Country's document list: each type, oldest first, with the group it goes in."""
    return {
        "country": one.country.value,
        "documents": [
            {
                "document_type": {
                    "id": row.document_type.public_id,
                    "name": row.document_type.name,
                },
                "group": row.group.value,
            }
            for row in one.listed
        ],
    }


def _person(staff: Staff | None) -> dict[str, object] | None:
    return None if staff is None else {"id": staff.public_id, "name": staff.name}


def _link_answer(document: Document) -> dict[str, object] | None:
    """The Application or Visa the Document is for, with the University it is about."""
    if document.application is not None:
        university = document.application.course.university
        return {
            "kind": "application",
            "id": document.application.public_id,
            "university": university.name,
        }
    if document.visa is not None:
        university = document.visa.application.course.university
        return {"kind": "visa", "id": document.visa.public_id, "university": university.name}
    return None


def _uploader(name: str, staff: Staff | None) -> dict[str, object] | None:
    return person_ref(staff, name) if name else None


def file_answer(file: DocumentFile) -> dict[str, object]:
    """One file of a Document: its shown name, type and size, and who added it when."""
    return {
        "id": file.public_id,
        "file_name": file.file_name,
        "content_type": file.content_type,
        "size": file.size,
        "uploaded_at": file.uploaded_at.isoformat(),
        "uploaded_by": _uploader(file.uploaded_by_name, file.uploaded_by),
    }


def _binned_file_answer(file: DocumentFile) -> dict[str, object]:
    """A file in the Bin: only its name and why; it can't be opened from here (issue 79)."""
    return {"id": file.public_id, "file_name": file.file_name, "bin": bin_answer(file)}


def document_answer(document: Document) -> dict[str, object]:
    """One Document: its files, how it was judged (one judgment for all), and its link.

    A Rejected Document says why; a linked one names its Application or Visa (spec 66, 68).
    Its files in the Bin are listed apart, by name only (issue 79).
    """
    files = list(document.files.all())
    return {
        "id": document.public_id,
        "files": [file_answer(file) for file in files if not file.is_in_bin],
        "binned_files": [_binned_file_answer(file) for file in files if file.is_in_bin],
        "verification": document.verification,
        "rejection_reason": document.rejection_reason,
        "link": _link_answer(document),
        "uploaded_at": document.uploaded_at.isoformat(),
        "uploaded_by": _uploader(document.uploaded_by_name, document.uploaded_by),
    }


def _in_bin_answer(document: Document) -> dict[str, object]:
    """A whole Document in the Bin: its file names and why; nothing can be opened (issue 79)."""
    return {
        "id": document.public_id,
        "files": [
            {"id": file.public_id, "file_name": file.file_name} for file in document.files.all()
        ],
        "bin": bin_answer(document),
    }


def _item_answer(item: ChecklistItem, documents: StudentChecklist) -> dict[str, object]:
    current = None if item.document is None else documents.documents[item.document.id]
    in_bin = [documents.documents[one.id] for one in item.in_bin]
    guide = documents.guides.get(item.type_id)
    return {
        "document_type": {"id": documents.type_ids[item.type_id], "name": item.name},
        "reason": item.reason,
        "is_required": item.is_required,
        "added_for_student": item.added_for_student,
        "group": None if item.group is None else item.group.value,
        "state": item.state.value,
        "document": None if current is None else document_answer(current),
        "in_bin": [_in_bin_answer(document) for document in in_bin],
        # The type's Document Example: what it should look like (spec 64). Never the Student's.
        "instruction": "" if guide is None else guide.instruction,
        "examples": [] if guide is None else examples_answer(guide.examples),
    }


def progress_answer(checklist: Checklist) -> dict[str, object]:
    """How many required types have a Document that isn't Rejected, of how many."""
    return {"done": checklist.done, "total": checklist.total}


def checklist_answer(
    documents: StudentChecklist,
    *,
    can_change: bool,
    deletes_for_good: bool = False,
    viewer_id: UUID | None = None,
) -> dict[str, object]:
    """The Student's checklist, the types asked for them alone, and what the viewer may do.

    `deletes_for_good`: an Admin's deletes skip the Bin. `viewer_id` lets the screen offer "Take
    it out" on what the viewer put in the Bin (the server checks again); None for a Platform Admin.
    """
    checklist = documents.checklist
    return {
        **progress_answer(checklist),
        "can_change": can_change,
        "deletes_for_good": deletes_for_good,
        "viewer_id": viewer_id,
        "asked": list(documents.asked),
        "items": [_item_answer(item, documents) for item in checklist.items],
    }


def paperwork_answer(one: Paperwork) -> dict[str, object]:
    """One Student on the Documents page, with their counts by state."""
    student = one.student
    counts = one.checklist.counts
    return {
        "student": {
            "id": student.public_id,
            "name": student.name,
            "stage": student.stage,
            "country": student.country,
            "assigned_counsellor": _person(student.assigned_counsellor),
        },
        **progress_answer(one.checklist),
        "rejected": counts.rejected,
        "waiting": counts.waiting,
        "missing": counts.missing,
        "verified": counts.verified,
    }


def paperwork_totals(found: list[Paperwork]) -> dict[str, int]:
    """The counts by state across every Student on the page's list (not only this page)."""
    counts = [one.checklist.counts for one in found]
    return {
        "rejected": sum(count.rejected for count in counts),
        "waiting": sum(count.waiting for count in counts),
        "missing": sum(count.missing for count in counts),
        "verified": sum(count.verified for count in counts),
    }


def bin_item_answer(item: BinItem) -> dict[str, object]:
    """One item on the Bin tab: a whole Document or one file, its Student, files, who and why."""
    document = item.document
    files = [item.file] if item.file is not None else list(document.files.all())
    student = document.student
    return {
        "kind": "document" if item.file is None else "file",
        "document": {"id": document.public_id, "name": document.document_type.name},
        "student": {"id": student.public_id, "name": student.name},
        "files": [{"id": file.public_id, "file_name": file.file_name} for file in files],
        **bin_answer(item.held),
    }
