"""A Student's document checklist, as pure functions (spec 64, 121; issues 40, 80). No database.

`required_types` lists what the Student must bring, each in a Document Group with the reason it is
there: the types every Student needs, then the Country lists that apply (see `StudentPlan`), then
those each Course they apply to asks for, then those asked for them alone. `build_checklist` adds
the state of the newest Document of each. Documents of other types follow as "Other documents",
without a group and without counting towards the total. A Document in the Bin counts as missing;
it is listed with its type, apart from the one in use (issue 79).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from documents.groups import DocumentGroup
from documents.verification import Verification

# How much each state asks of the counsellor, to order the Documents page (spec 64a).
_ATTENTION_REJECTED = 3
_ATTENTION_WAITING = 2
_ATTENTION_MISSING = 1


class DocumentState(StrEnum):
    """Where one checklist item is, as the checklist shows it."""

    NOT_RECEIVED = "not_received"
    WAITING = "waiting"  # Pending Verification: waiting for a check.
    VERIFIED = "verified"  # Checked and correct.
    REJECTED = "rejected"  # A new copy is needed.


_STATE_OF: dict[Verification, DocumentState] = {
    Verification.PENDING: DocumentState.WAITING,
    Verification.VERIFIED: DocumentState.VERIFIED,
    Verification.REJECTED: DocumentState.REJECTED,
}


ADDED_FOR_STUDENT_REASON = "Added for this Student"


@dataclass(frozen=True)
class NamedType:
    """A Document Type by its id and name, and the Document Group it usually goes in."""

    id: int
    name: str
    group: DocumentGroup = DocumentGroup.APPLYING


@dataclass(frozen=True)
class ListedType:
    """A Document Type on a Country's list, in the Document Group that Country puts it."""

    type: NamedType
    group: DocumentGroup


@dataclass(frozen=True)
class CountryList:
    """What one Country asks for, by its shown name (empty for a Country with no list)."""

    country: str
    types: Sequence[ListedType]

    def in_group(self, *, applying: bool) -> list[ListedType]:
        """Its Applying types, or all the others (Visa and Statements)."""
        return [t for t in self.types if (t.group == DocumentGroup.APPLYING) == applying]


@dataclass(frozen=True)
class AppliedCourse:
    """The Course of one of the Student's Applications, and what it asks for."""

    name: str
    types: Sequence[NamedType]


@dataclass(frozen=True)
class StudentPlan:
    """Where the Student is going, which decides the Country lists that apply (spec 121).

    `applying` holds the list of each Country they apply to (Rejected Applications don't count);
    with none, their preferred country's whole list applies. `chosen` is the Chosen Offer's
    Country: only its Visa and Statements lists apply.
    """

    preferred: CountryList
    applying: Sequence[CountryList]
    chosen: CountryList | None
    courses: Sequence[AppliedCourse]


@dataclass(frozen=True)
class RequiredType:
    """A Document Type the Student must bring, its group, and why ("" when every Student needs it).

    `added_for_student` marks one asked for this Student alone, which can be taken off again.
    """

    id: int
    name: str
    reason: str = ""
    added_for_student: bool = False
    group: DocumentGroup = DocumentGroup.APPLYING


def _listed(country: str, types: Sequence[ListedType]) -> list[RequiredType]:
    return [RequiredType(t.type.id, t.type.name, f"For {country}", group=t.group) for t in types]


def _usual(types: Sequence[NamedType], reason: str, *, own: bool = False) -> list[RequiredType]:
    return [RequiredType(t.id, t.name, reason, own, t.group) for t in types]


def required_types(
    everyone: Sequence[NamedType], plan: StudentPlan, asked: Sequence[NamedType]
) -> list[RequiredType]:
    """What the Student must bring, in checklist order, each with its group and reason.

    Duplicates are kept here; `build_checklist` lists each type once with its first reason.
    """
    required = _usual(everyone, "")
    if not plan.applying:
        required += _listed(plan.preferred.country, plan.preferred.types)
    for one in plan.applying:
        required += _listed(one.country, one.in_group(applying=True))
    if plan.chosen is not None:
        required += _listed(plan.chosen.country, plan.chosen.in_group(applying=False))
    for course in plan.courses:
        required += _usual(course.types, f"For {course.name}")
    required += _usual(asked, ADDED_FOR_STUDENT_REASON, own=True)
    return required


@dataclass(frozen=True)
class HeldDocument:
    """A Document the Student has: its type, how it was judged, and whether it is in the Bin."""

    id: int
    type_id: int
    type_name: str
    verification: Verification
    in_bin: bool = False


@dataclass(frozen=True)
class ChecklistItem:
    """One line of the checklist: a type, why it is there, and its newest Document if any.

    `in_bin` holds the type's Documents waiting in the Bin, newest first: shown, never used.
    `group` is None for one not on the checklist ("Other documents").
    """

    type_id: int
    name: str
    reason: str
    is_required: bool
    state: DocumentState
    document: HeldDocument | None
    added_for_student: bool = False
    in_bin: tuple[HeldDocument, ...] = ()
    group: DocumentGroup | None = DocumentGroup.APPLYING


@dataclass(frozen=True)
class ChecklistCounts:
    """How many required types are in each state."""

    verified: int
    waiting: int
    rejected: int
    missing: int

    @property
    def attention(self) -> int:
        """How much the paperwork needs looking at: rejected first, then waiting, then missing."""
        return (
            self.rejected * _ATTENTION_REJECTED
            + self.waiting * _ATTENTION_WAITING
            + self.missing * _ATTENTION_MISSING
        )


@dataclass(frozen=True)
class Checklist:
    """The checklist, and how many required types have a Document that isn't Rejected."""

    items: tuple[ChecklistItem, ...]
    done: int
    total: int

    @property
    def counts(self) -> ChecklistCounts:
        """The required types by state."""
        required = [item.state for item in self.items if item.is_required]
        return ChecklistCounts(
            verified=required.count(DocumentState.VERIFIED),
            waiting=required.count(DocumentState.WAITING),
            rejected=required.count(DocumentState.REJECTED),
            missing=required.count(DocumentState.NOT_RECEIVED),
        )


def build_checklist(
    required: Sequence[RequiredType], documents: Sequence[HeldDocument]
) -> Checklist:
    """Build the checklist from the required types (in order) and the Documents (oldest first).

    A type asked for more than once is listed once, with its first reason. A type counts as done
    when its newest Document not in the Bin isn't Rejected.
    """
    newest = {document.type_id: document for document in documents if not document.in_bin}
    binned = _in_bin_by_type(documents)
    items: list[ChecklistItem] = []
    seen: set[int] = set()
    for wanted in required:
        if wanted.id not in seen:
            seen.add(wanted.id)
            items.append(_item(wanted, newest.get(wanted.id), binned.get(wanted.id, [])))
    for document in documents:
        if document.type_id not in seen:
            seen.add(document.type_id)
            other = RequiredType(document.type_id, document.type_name)
            in_bin = binned.get(document.type_id, [])
            items.append(_item(other, newest.get(document.type_id), in_bin, is_required=False))
    required_items = [item for item in items if item.is_required]
    done = sum(
        item.state in {DocumentState.WAITING, DocumentState.VERIFIED} for item in required_items
    )
    return Checklist(items=tuple(items), done=done, total=len(required_items))


def _in_bin_by_type(documents: Sequence[HeldDocument]) -> dict[int, list[HeldDocument]]:
    """The Documents in the Bin, by type, newest first."""
    binned: dict[int, list[HeldDocument]] = {}
    for document in reversed(documents):
        if document.in_bin:
            binned.setdefault(document.type_id, []).append(document)
    return binned


def _item(
    wanted: RequiredType,
    document: HeldDocument | None,
    in_bin: Sequence[HeldDocument],
    *,
    is_required: bool = True,
) -> ChecklistItem:
    state = DocumentState.NOT_RECEIVED if document is None else _STATE_OF[document.verification]
    return ChecklistItem(
        type_id=wanted.id,
        name=wanted.name,
        reason=wanted.reason,
        is_required=is_required,
        state=state,
        document=document,
        added_for_student=wanted.added_for_student,
        in_bin=tuple(in_bin),
        group=wanted.group if is_required else None,
    )
