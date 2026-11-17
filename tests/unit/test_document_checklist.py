"""A Student's document checklist, as one pure function (spec 64; issue 16)."""

from __future__ import annotations

from documents.checklist import (
    AppliedCourse,
    ChecklistItem,
    CountryList,
    DocumentState,
    HeldDocument,
    ListedType,
    NamedType,
    RequiredType,
    StudentPlan,
    build_checklist,
    required_types,
)
from documents.groups import DocumentGroup
from documents.verification import Verification

PASSPORT = RequiredType(id=1, name="Passport")
CITIZENSHIP = RequiredType(id=2, name="Citizenship")
TRANSCRIPT = RequiredType(id=3, name="+2 Transcript")


def held(
    document_id: int,
    type_id: int,
    verification: Verification = Verification.PENDING,
    type_name: str = "",
) -> HeldDocument:
    """A Document the Student has, of one type."""
    return HeldDocument(
        id=document_id, type_id=type_id, type_name=type_name, verification=verification
    )


def states(items: tuple[ChecklistItem, ...]) -> list[tuple[str, DocumentState]]:
    """Each item's name and state, in order."""
    return [(item.name, item.state) for item in items]


class TestRequiredTypes:
    def test_nothing_received_lists_every_type_as_not_received(self) -> None:
        checklist = build_checklist([PASSPORT, CITIZENSHIP], [])

        assert states(checklist.items) == [
            ("Passport", DocumentState.NOT_RECEIVED),
            ("Citizenship", DocumentState.NOT_RECEIVED),
        ]
        assert (checklist.done, checklist.total) == (0, 2)

    def test_each_verification_gives_its_state(self) -> None:
        checklist = build_checklist(
            [PASSPORT, CITIZENSHIP, TRANSCRIPT],
            [
                held(10, PASSPORT.id, Verification.PENDING),
                held(11, CITIZENSHIP.id, Verification.VERIFIED),
                held(12, TRANSCRIPT.id, Verification.REJECTED),
            ],
        )

        assert states(checklist.items) == [
            ("Passport", DocumentState.WAITING),
            ("Citizenship", DocumentState.VERIFIED),
            ("+2 Transcript", DocumentState.REJECTED),
        ]

    def test_a_rejected_document_is_not_counted_as_received(self) -> None:
        checklist = build_checklist(
            [PASSPORT, CITIZENSHIP, TRANSCRIPT],
            [
                held(10, PASSPORT.id, Verification.PENDING),
                held(11, CITIZENSHIP.id, Verification.VERIFIED),
                held(12, TRANSCRIPT.id, Verification.REJECTED),
            ],
        )

        assert (checklist.done, checklist.total) == (2, 3)

    def test_the_newest_document_of_a_type_decides_its_state(self) -> None:
        checklist = build_checklist(
            [PASSPORT],
            [held(10, PASSPORT.id, Verification.REJECTED), held(11, PASSPORT.id)],
        )

        [item] = checklist.items
        assert item.state == DocumentState.WAITING
        assert item.document == held(11, PASSPORT.id)
        assert checklist.done == 1

    def test_an_item_carries_its_reason(self) -> None:
        for_deakin = RequiredType(id=4, name="Genuine Student statement", reason="For Deakin")

        [everyone, extra] = build_checklist([PASSPORT, for_deakin], []).items

        assert (everyone.reason, extra.reason) == ("", "For Deakin")

    def test_a_type_asked_for_twice_is_listed_once_with_its_first_reason(self) -> None:
        again = RequiredType(id=PASSPORT.id, name="Passport", reason="For Deakin")

        checklist = build_checklist([PASSPORT, again], [])

        assert [(item.name, item.reason) for item in checklist.items] == [("Passport", "")]
        assert checklist.total == 1

    def test_an_empty_checklist_has_nothing_to_do(self) -> None:
        checklist = build_checklist([], [])

        assert checklist.items == ()
        assert (checklist.done, checklist.total) == (0, 0)


class TestDocumentsOfOtherTypes:
    def test_they_follow_the_checklist_without_counting(self) -> None:
        checklist = build_checklist(
            [PASSPORT],
            [held(10, 9, Verification.VERIFIED, type_name="Police report"), held(11, PASSPORT.id)],
        )

        assert states(checklist.items) == [
            ("Passport", DocumentState.WAITING),
            ("Police report", DocumentState.VERIFIED),
        ]
        assert [item.is_required for item in checklist.items] == [True, False]
        assert (checklist.done, checklist.total) == (1, 1)

    def test_each_is_listed_once_by_its_newest_document(self) -> None:
        checklist = build_checklist(
            [],
            [
                held(10, 9, Verification.REJECTED, type_name="Police report"),
                held(11, 9, type_name="Police report"),
            ],
        )

        [item] = checklist.items
        assert (item.state, item.document) == (
            DocumentState.WAITING,
            held(11, 9, type_name="Police report"),
        )


class TestCounts:
    def test_counts_cover_only_the_required_types(self) -> None:
        checklist = build_checklist(
            [PASSPORT, CITIZENSHIP, TRANSCRIPT, RequiredType(id=5, name="Bank Statement")],
            [
                held(10, PASSPORT.id, Verification.VERIFIED),
                held(11, CITIZENSHIP.id),
                held(12, TRANSCRIPT.id, Verification.REJECTED),
                held(13, 9, Verification.REJECTED, type_name="Police report"),
            ],
        )

        counts = checklist.counts
        assert (counts.verified, counts.waiting, counts.rejected, counts.missing) == (1, 1, 1, 1)

    def test_attention_weighs_rejected_then_waiting_then_missing(self) -> None:
        one_rejected = build_checklist([PASSPORT], [held(1, PASSPORT.id, Verification.REJECTED)])
        one_waiting = build_checklist([PASSPORT], [held(1, PASSPORT.id)])
        one_missing = build_checklist([PASSPORT], [])
        all_correct = build_checklist([PASSPORT], [held(1, PASSPORT.id, Verification.VERIFIED)])

        scores = [
            checklist.counts.attention
            for checklist in (one_rejected, one_waiting, one_missing, all_correct)
        ]
        assert scores == [3, 2, 1, 0]


APPLYING = DocumentGroup.APPLYING
VISA = DocumentGroup.VISA
STATEMENTS = DocumentGroup.STATEMENTS
POLICE = NamedType(id=20, name="Police report", group=VISA)
GS = NamedType(id=21, name="Genuine Student statement", group=STATEMENTS)
CV = NamedType(id=22, name="CV")
PORTFOLIO = NamedType(id=23, name="Portfolio")
BANK = NamedType(id=24, name="Bank Statement", group=VISA)
LOE = NamedType(id=25, name="Letter of explanation", group=STATEMENTS)
BIRTH = NamedType(id=26, name="Birth certificate", group=VISA)

AUSTRALIA = CountryList(
    "Australia", (ListedType(GS, STATEMENTS), ListedType(BANK, VISA), ListedType(CV, APPLYING))
)
CANADA = CountryList("Canada", (ListedType(LOE, STATEMENTS), ListedType(BANK, VISA)))
# Japan asks for a birth certificate when applying, though it is usually a Visa document.
JAPAN = CountryList("Japan", (ListedType(BIRTH, APPLYING), ListedType(POLICE, VISA)))
NOTHING = CountryList("Other", ())


def plan(
    preferred: CountryList = NOTHING,
    applying: tuple[CountryList, ...] = (),
    chosen: CountryList | None = None,
    courses: tuple[AppliedCourse, ...] = (),
) -> StudentPlan:
    """Where the Student is going: preferred country, countries applied to, the Chosen Offer's."""
    return StudentPlan(preferred=preferred, applying=applying, chosen=chosen, courses=courses)


def reasons(required: list[RequiredType]) -> list[tuple[str, str, bool]]:
    """Each required type's name, reason and whether it was added for the Student."""
    return [(r.name, r.reason, r.added_for_student) for r in required]


def grouped(required: list[RequiredType]) -> list[tuple[str, str, DocumentGroup | None]]:
    """Each type the checklist lists (once), with its reason and Document Group."""
    return [(i.name, i.reason, i.group) for i in build_checklist(required, []).items]


class TestWhatIsRequired:
    """Issue 80: everyone's types, the Country lists that apply, each Course's, then theirs."""

    def test_with_nothing_extra_only_everyones_types_are_required(self) -> None:
        required = required_types([NamedType(1, "Passport")], plan(), [])

        assert reasons(required) == [("Passport", "", False)]

    def test_before_any_application_the_preferred_countrys_whole_list_applies(self) -> None:
        required = required_types([], plan(preferred=AUSTRALIA), [])

        assert grouped(required) == [
            ("Genuine Student statement", "For Australia", STATEMENTS),
            ("Bank Statement", "For Australia", VISA),
            ("CV", "For Australia", APPLYING),
        ]

    def test_while_applying_only_the_applying_lists_of_each_country_apply(self) -> None:
        required = required_types([], plan(AUSTRALIA, applying=(JAPAN, AUSTRALIA)), [])

        assert grouped(required) == [
            ("Birth certificate", "For Japan", APPLYING),
            ("CV", "For Australia", APPLYING),
        ]

    def test_the_preferred_country_no_longer_counts_once_applying(self) -> None:
        required = required_types([], plan(CANADA, applying=(JAPAN,)), [])

        assert [r.name for r in required] == ["Birth certificate"]

    def test_with_a_chosen_offer_visa_and_statements_come_only_from_its_country(self) -> None:
        required = required_types(
            [], plan(CANADA, applying=(AUSTRALIA, JAPAN), chosen=AUSTRALIA), []
        )

        assert grouped(required) == [
            ("CV", "For Australia", APPLYING),
            ("Birth certificate", "For Japan", APPLYING),
            ("Genuine Student statement", "For Australia", STATEMENTS),
            ("Bank Statement", "For Australia", VISA),
        ]

    def test_changing_the_chosen_offer_changes_the_visa_and_statements_lists(self) -> None:
        before = required_types([], plan(applying=(AUSTRALIA, JAPAN), chosen=AUSTRALIA), [])
        after = required_types([], plan(applying=(AUSTRALIA, JAPAN), chosen=JAPAN), [])

        assert {r.name for r in before} - {r.name for r in after} == {
            "Genuine Student statement",
            "Bank Statement",
        }
        assert ("Police report", "For Japan", VISA) in grouped(after)

    def test_courses_and_the_students_own_use_each_types_usual_group(self) -> None:
        required = required_types(
            [NamedType(1, "Passport")],
            plan(applying=(NOTHING,), courses=(AppliedCourse("Master of Nursing", (POLICE,)),)),
            [GS],
        )

        assert grouped(required) == [
            ("Passport", "", APPLYING),
            ("Police report", "For Master of Nursing", VISA),
            ("Genuine Student statement", "Added for this Student", STATEMENTS),
        ]

    def test_types_asked_for_the_student_come_last(self) -> None:
        required = required_types([], plan(AUSTRALIA), [PORTFOLIO])

        assert reasons(required)[-1] == ("Portfolio", "Added for this Student", True)

    def test_a_type_asked_for_twice_keeps_its_first_reason_and_group(self) -> None:
        required = required_types(
            [NamedType(1, "Passport")],
            plan(
                applying=(JAPAN,),
                chosen=JAPAN,
                courses=(AppliedCourse("Master of IT", (BIRTH, NamedType(1, "Passport"))),),
            ),
            [BIRTH],
        )

        checklist = build_checklist(required, [])

        assert [(i.name, i.reason, i.group) for i in checklist.items] == [
            ("Passport", "", APPLYING),
            ("Birth certificate", "For Japan", APPLYING),
            ("Police report", "For Japan", VISA),
        ]
        assert checklist.total == 3
        assert checklist.items[1].added_for_student is False

    def test_an_item_added_for_the_student_says_so(self) -> None:
        checklist = build_checklist(
            required_types([], plan(), [PORTFOLIO]), [held(9, PORTFOLIO.id)]
        )

        [item] = checklist.items
        assert (item.reason, item.added_for_student, item.state) == (
            "Added for this Student",
            True,
            DocumentState.WAITING,
        )


class TestOtherDocuments:
    """Issue 80: an uploaded Document no longer on the checklist is listed apart, never lost."""

    def test_documents_of_types_no_longer_asked_for_follow_without_counting(self) -> None:
        checklist = build_checklist(
            required_types([], plan(), []), [held(9, CV.id, Verification.VERIFIED, "CV")]
        )

        [item] = checklist.items
        assert (item.name, item.is_required, item.added_for_student) == ("CV", False, False)
        assert item.group is None
        assert (checklist.done, checklist.total) == (0, 0)

    def test_a_document_moves_back_when_its_type_is_needed_again(self) -> None:
        uploaded = [held(9, BANK.id, Verification.VERIFIED, "Bank Statement")]
        applying = build_checklist(required_types([], plan(applying=(AUSTRALIA,)), []), uploaded)
        chosen = build_checklist(
            required_types([], plan(applying=(AUSTRALIA,), chosen=AUSTRALIA), []), uploaded
        )

        [other] = [i for i in applying.items if i.type_id == BANK.id]
        [back] = [i for i in chosen.items if i.type_id == BANK.id]
        assert (other.is_required, other.group) == (False, None)
        assert (back.is_required, back.group, back.state) == (True, VISA, DocumentState.VERIFIED)

    def test_one_upload_counts_for_every_list_that_asks_for_its_type(self) -> None:
        checklist = build_checklist(
            required_types(
                [],
                plan(applying=(AUSTRALIA, AUSTRALIA), courses=(AppliedCourse("X", (CV,)),)),
                [],
            ),
            [held(9, CV.id, Verification.VERIFIED, "CV")],
        )

        assert (checklist.done, checklist.total) == (1, 1)


def in_bin(document_id: int, type_id: int, type_name: str = "") -> HeldDocument:
    """A Document of the Student's waiting in the Bin (issue 79)."""
    return HeldDocument(document_id, type_id, type_name, Verification.VERIFIED, in_bin=True)


class TestTheBin:
    def test_a_document_in_the_bin_counts_as_missing_but_is_listed(self) -> None:
        checklist = build_checklist([PASSPORT], [in_bin(5, PASSPORT.id)])

        [item] = checklist.items
        assert (item.state, item.document) == (DocumentState.NOT_RECEIVED, None)
        assert [one.id for one in item.in_bin] == [5]
        assert (checklist.done, checklist.counts.missing) == (0, 1)

    def test_the_newest_copy_not_in_the_bin_is_the_one_in_use(self) -> None:
        checklist = build_checklist(
            [PASSPORT],
            [held(1, PASSPORT.id, Verification.REJECTED), held(2, PASSPORT.id), in_bin(3, 1)],
        )

        [item] = checklist.items
        assert item.document is not None
        assert (item.document.id, item.state) == (2, DocumentState.WAITING)
        assert [one.id for one in item.in_bin] == [3]

    def test_several_in_the_bin_are_newest_first(self) -> None:
        checklist = build_checklist([PASSPORT], [in_bin(1, PASSPORT.id), in_bin(4, PASSPORT.id)])

        assert [one.id for one in checklist.items[0].in_bin] == [4, 1]

    def test_a_type_no_longer_asked_for_with_only_a_binned_document_follows(self) -> None:
        checklist = build_checklist([], [in_bin(9, 7, "CV")])

        [item] = checklist.items
        assert (item.name, item.is_required, item.document, item.state) == (
            "CV",
            False,
            None,
            DocumentState.NOT_RECEIVED,
        )
        assert [one.id for one in item.in_bin] == [9]
        assert (checklist.done, checklist.total) == (0, 0)
