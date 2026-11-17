"""Communication: one SMS or email to many Students at once, a filtered list or a whole Batch.

Spec 90-91; issue 26. The SMS company and the email server are fakes (tests/fakes.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.test import override_settings
from django_tenants.utils import tenant_context
from rest_framework import status

from communication.models import Message
from config.settings import BULK_MAX_STUDENTS
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs
from tests.fakes import SMS_REFUSED_FOR_NUMBER, FakeEmailGateway, FakeSmsGateway
from tests.integration.test_batches_integration import make_batch
from tests.integration.test_communication_integration import SETTINGS, save_settings
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

BULK_URL = "/api/messages/bulk/"
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
NOT_FOR_FRONT_DESK = {"detail": "Only Admins, counsellors and teachers can see classes."}
SMS_NOT_SET_UP = {"channel": ["SMS isn't set up yet. An Admin can set it up in Configuration."]}
NOBODY = {"detail": "No Students to send to."}
TOO_MANY = {
    "detail": f"Message at most {BULK_MAX_STUDENTS} Students at once. Narrow the list first."
}
ONE_OF = {"detail": "Choose a class or a Student list."}
TOO_SLOW = "Not sent: sending took too long. Send again to this Student."


def send_bulk(office: ConsultancyContext, body: object) -> _MonkeyPatchedResponse:
    """POST one message to many Students, as given."""
    return office.client.post(BULK_URL, body, format="json")


def sms(body: str = "Namaste, class is at 7.", **to: object) -> dict[str, object]:
    """An SMS to the Students named by `to` (a batch, or a Student list's filters)."""
    return {"channel": "sms", "body": body, **to}


def stored_messages(office: ConsultancyContext) -> list[Message]:
    """Every stored message, oldest first, with its Student."""
    with tenant_context(office.consultancy):
        return list(Message.objects.select_related("student").order_by("pk"))


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with SMS, email and classes switched on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.COMMUNICATION, Module.BATCHES})
    return office


@pytest.fixture
def admin(abc: ConsultancyContext) -> Staff:
    """The office's Admin."""
    return named_staff(abc, "Asha Admin", Role.ADMIN)


@pytest.fixture
def counsellor(abc: ConsultancyContext) -> Staff:
    """A Counsellor."""
    return named_staff(abc, "Ram Counsellor", Role.COUNSELLOR)


@pytest.fixture
def teacher(abc: ConsultancyContext) -> Staff:
    """A Teacher."""
    return named_staff(abc, "Tara Teacher", Role.TEACHER)


@pytest.fixture
def set_up(abc: ConsultancyContext, admin: Staff) -> None:
    """The Admin has saved the SMS and email accounts, and is logged in."""
    log_in(abc, admin)
    assert save_settings(abc, SETTINGS).status_code == status.HTTP_200_OK


@pytest.fixture
def students(abc: ConsultancyContext, counsellor: Staff) -> dict[str, Student]:
    """Three Onboarded Students of Ram's, one with a landline, and an unassigned Lead."""
    return {
        "sita": make_student(
            abc, counsellor, Stage.COUNSELLING, name="Sita Gurung", email="sita@example.com"
        ),
        "hari": make_student(
            abc, counsellor, Stage.COUNSELLING, name="Hari Thapa", phone="9841000002"
        ),
        "gita": make_student(
            abc, counsellor, Stage.COUNSELLING, name="Gita Rai", phone="014412345"
        ),
        "maya": make_student(abc, None, Stage.LEAD, name="Maya Lama", phone="9841000004"),
    }


@pytest.fixture
def ielts(abc: ConsultancyContext, teacher: Staff, students: dict[str, Student]) -> int:
    """The IELTS Morning class, with Sita and Maya in it; returns its id."""
    batch = make_batch(abc, teacher, (students["sita"], students["maya"]), name="IELTS Morning")
    return batch.pk


class TestSendingToAStudentList:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_every_student_matching_the_filters_gets_one(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student], role: Role
    ) -> None:
        sender = abc.log_in_as(role)

        response = send_bulk(abc, sms(students={"stage": "counselling"}))

        assert response.status_code == status.HTTP_201_CREATED
        messages = stored_messages(abc)
        assert sorted(m.student.name for m in messages) == ["Gita Rai", "Hari Thapa", "Sita Gurung"]
        assert {m.sent_by_id for m in messages} == {sender.pk}
        assert sorted(s.to for s in FakeSmsGateway.sent) == ["9841000001", "9841000002"]
        answer = response.json()
        assert (answer["sent"], answer["failed"]) == (2, 1)
        [failed] = answer["failed_messages"]
        assert failed["student"]["name"] == "Gita Rai"
        assert failed["error"] == "014412345 isn't a 10-digit Nepali mobile number."
        assert failed["status"] == "failed"

    def test_the_search_source_and_counsellor_filters_apply(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student]
    ) -> None:
        responses = [
            send_bulk(abc, sms(students={"q": "hari"})),
            send_bulk(abc, sms(students={"counsellor": "none", "source": "walk_in"})),
        ]

        assert [r.json()["sent"] for r in responses] == [1, 1]
        assert [s.to for s in FakeSmsGateway.sent] == ["9841000002", "9841000004"]

    def test_assigned_to_me_means_the_sender(
        self, abc: ConsultancyContext, set_up: None, counsellor: Staff, students: dict[str, Student]
    ) -> None:
        log_in(abc, counsellor)

        response = send_bulk(abc, sms(students={"counsellor": "me"}))

        assert (response.json()["sent"], response.json()["failed"]) == (2, 1)

    def test_no_filters_means_every_student(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student]
    ) -> None:
        response = send_bulk(abc, sms(students={}))

        assert len(stored_messages(abc)) == len(students)
        assert response.json()["sent"] == 3

    def test_an_email_goes_to_every_student_and_those_without_an_address_fail(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student]
    ) -> None:
        response = send_bulk(
            abc, {"channel": "email", "body": "Namaste,\n\nOffice closed.", "students": {}}
        )

        assert (response.json()["sent"], response.json()["failed"]) == (1, 3)
        assert [e.to for e in FakeEmailGateway.sent] == ["sita@example.com"]
        assert {m["error"] for m in response.json()["failed_messages"]} == {
            "This Student has no email address."
        }


class TestSendingToABatch:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR])
    def test_everyone_in_the_class_gets_one(
        self, abc: ConsultancyContext, set_up: None, ielts: int, role: Role
    ) -> None:
        abc.log_in_as(role)

        response = send_bulk(abc, sms(batch=ielts))

        assert response.status_code == status.HTTP_201_CREATED
        assert (response.json()["sent"], response.json()["failed"]) == (2, 0)
        assert response.json()["failed_messages"] == []
        assert sorted(m.student.name for m in stored_messages(abc)) == ["Maya Lama", "Sita Gurung"]

    def test_front_desk_cant_message_a_class(
        self, abc: ConsultancyContext, set_up: None, ielts: int
    ) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        responses = [send_bulk(abc, sms(batch=ielts)), abc.client.get(BULK_URL, {"batch": ielts})]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 2
        assert all(r.json() == NOT_FOR_FRONT_DESK for r in responses)
        assert stored_messages(abc) == []

    def test_an_unknown_class_is_not_found(self, abc: ConsultancyContext, set_up: None) -> None:
        responses = [
            send_bulk(abc, sms(batch=999_999)),
            abc.client.get(BULK_URL, {"batch": 999_999}),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2

    def test_with_classes_switched_off_a_class_is_not_found_and_lists_still_work(
        self, abc: ConsultancyContext, set_up: None, ielts: int
    ) -> None:
        switch_modules(abc.consultancy, {Module.COMMUNICATION})

        responses = [send_bulk(abc, sms(batch=ielts)), abc.client.get(BULK_URL, {"batch": ielts})]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        assert stored_messages(abc) == []
        assert send_bulk(abc, sms(students={})).status_code == status.HTTP_201_CREATED
        switch_modules(abc.consultancy, {Module.COMMUNICATION, Module.BATCHES})
        assert send_bulk(abc, sms(batch=ielts)).json()["sent"] == 2


class TestOneFailureNeverStopsTheRest:
    def test_a_refusal_for_one_number_fails_only_that_student(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student]
    ) -> None:
        FakeSmsGateway.refused_numbers.add("9841000001")

        response = send_bulk(abc, sms(students={"stage": "counselling"}))

        answer = response.json()
        assert (answer["sent"], answer["failed"]) == (1, 2)
        assert {m["student"]["name"]: m["error"] for m in answer["failed_messages"]} == {
            "Sita Gurung": SMS_REFUSED_FOR_NUMBER,
            "Gita Rai": "014412345 isn't a 10-digit Nepali mobile number.",
        }
        assert [s.to for s in FakeSmsGateway.sent] == ["9841000002"]
        assert len(stored_messages(abc)) == 3

    def test_each_student_has_their_own_record(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student]
    ) -> None:
        send_bulk(abc, sms("Hello all", students={"stage": "counselling"}))

        rows = {(m.student.name, m.status, m.sent_to, m.body) for m in stored_messages(abc)}
        assert rows == {
            ("Sita Gurung", "sent", "9841000001", "Hello all"),
            ("Hari Thapa", "sent", "9841000002", "Hello all"),
            ("Gita Rai", "failed", "014412345", "Hello all"),
        }

    @override_settings(BULK_SEND_SECONDS=0)
    def test_students_not_reached_in_time_are_recorded_as_failed(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student]
    ) -> None:
        response = send_bulk(abc, sms(students={"stage": "counselling"}))

        assert (response.json()["sent"], response.json()["failed"]) == (0, 3)
        assert FakeSmsGateway.sent == []
        assert {m.error for m in stored_messages(abc)} == {TOO_SLOW}

    def test_a_channel_not_set_up_is_refused_and_nothing_recorded(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        log_in(abc, admin)

        response = send_bulk(abc, sms(students={}))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == SMS_NOT_SET_UP
        assert stored_messages(abc) == []


class TestHowManyItGoesTo:
    def test_counts_a_student_list_and_a_class(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student], ielts: int
    ) -> None:
        answers = [
            abc.client.get(BULK_URL, {"stage": "counselling"}),
            abc.client.get(BULK_URL, {"q": "maya"}),
            abc.client.get(BULK_URL),
            abc.client.get(BULK_URL, {"batch": ielts}),
        ]

        assert [r.status_code for r in answers] == [status.HTTP_200_OK] * 4
        assert [r.json() for r in answers] == [
            {"count": 3, "max": BULK_MAX_STUDENTS},
            {"count": 1, "max": BULK_MAX_STUDENTS},
            {"count": 4, "max": BULK_MAX_STUDENTS},
            {"count": 2, "max": BULK_MAX_STUDENTS},
        ]
        assert stored_messages(abc) == []

    @pytest.mark.parametrize(
        "query",
        [
            {"stage": "sleeping"},
            {"source": "pigeon"},
            {"counsellor": "someone"},
            {"batch": "two"},
            {"batch": "0"},
            {"batch": "1", "stage": "lead"},
            {"page": "1"},
            {"colour": "red"},
            {"q": "x" * 101},
        ],
    )
    def test_bad_queries_are_refused(
        self, abc: ConsultancyContext, set_up: None, query: dict[str, str]
    ) -> None:
        response = abc.client.get(BULK_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestRefusedBulkMessages:
    @pytest.fixture(autouse=True)
    def ready(self, set_up: None, students: dict[str, Student]) -> None:
        """SMS and email are set up, the Admin is logged in, and there are Students."""

    def _refused(self, abc: ConsultancyContext, body: object) -> dict[str, Any]:
        response = send_bulk(abc, body)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_messages(abc) == []
        assert FakeSmsGateway.sent == []
        assert FakeEmailGateway.sent == []
        answer: dict[str, Any] = response.json()
        return answer

    def test_unknown_fields_are_refused(self, abc: ConsultancyContext) -> None:
        assert self._refused(abc, sms(students={}, to="9800000000")) == {
            "detail": ["Unknown field."]
        }
        assert self._refused(abc, sms(students={"page": 2})) == {
            "students": {"detail": ["Unknown field."]}
        }

    def test_a_class_or_a_student_list_is_needed_but_not_both(
        self, abc: ConsultancyContext, ielts: int
    ) -> None:
        assert self._refused(abc, sms()) == ONE_OF
        assert self._refused(abc, sms(batch=ielts, students={})) == ONE_OF

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"body": "Hi", "students": {}}, "channel"),
            ({"channel": "sms", "students": {}}, "body"),
            ({"channel": "whatsapp", "body": "Hi", "students": {}}, "channel"),
            ({"channel": "sms", "body": "", "students": {}}, "body"),
            ({"channel": "sms", "body": 42, "students": {}}, "body"),
            ({"channel": "sms", "body": "x" * 801, "students": {}}, "body"),
            ({"channel": "email", "body": "x" * 5001, "students": {}}, "body"),
            ({"channel": "sms", "body": "bell\x07", "students": {}}, "body"),
            (sms(batch="one"), "batch"),
            (sms(batch=True), "batch"),
            (sms(batch=1.5), "batch"),
            (sms(batch=0), "batch"),
            (sms(batch=None), "batch"),
            (sms(students=[]), "students"),
            (sms(students="all"), "students"),
            (sms(students=None), "students"),
            (sms(students={"stage": "sleeping"}), "students"),
            (sms(students={"source": "pigeon"}), "students"),
            (sms(students={"counsellor": "x"}), "students"),
            (sms(students={"q": "x" * 101}), "students"),
            (sms(students={"q": 5}), "students"),
        ],
    )
    def test_wrong_input_is_refused(
        self, abc: ConsultancyContext, body: dict[str, object], field: str
    ) -> None:
        assert field in self._refused(abc, body)

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_an_injection_shaped_search_finds_nobody(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        assert self._refused(abc, sms(students={"q": text})) == NOBODY

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_sent_and_kept_exactly_as_typed(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        send_bulk(abc, sms(text, students={"stage": "counselling"}))

        assert {m.body for m in stored_messages(abc)} == {text}
        assert {s.text for s in FakeSmsGateway.sent} == {text}

    def test_nobody_matching_is_refused(self, abc: ConsultancyContext) -> None:
        assert self._refused(abc, sms(students={"stage": "visa"})) == NOBODY

    def test_an_empty_class_is_refused(self, abc: ConsultancyContext, teacher: Staff) -> None:
        empty = make_batch(abc, teacher, name="PTE Evening")

        assert self._refused(abc, sms(batch=empty.pk)) == NOBODY

    @override_settings(BULK_MAX_STUDENTS=2)
    def test_too_many_students_are_refused(self, abc: ConsultancyContext) -> None:
        answer = self._refused(abc, sms(students={"stage": "counselling"}))

        assert answer == {"detail": "Message at most 2 Students at once. Narrow the list first."}
        assert abc.client.get(BULK_URL, {"stage": "counselling"}).json() == {"count": 3, "max": 2}

    @pytest.mark.parametrize("body", [["sms"], "sms", 7, None])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        self._refused(abc, body)

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        response = abc.client.post(BULK_URL, {"channel": "sms", "body": "Hi"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class TestBulkAccess:
    def test_teachers_cant_send_or_count(
        self, abc: ConsultancyContext, set_up: None, ielts: int
    ) -> None:
        abc.log_in_as(Role.TEACHER)

        responses = [
            send_bulk(abc, sms(batch=ielts)),
            send_bulk(abc, sms(students={})),
            abc.client.get(BULK_URL),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 3
        assert all(r.json() == NOT_FOR_TEACHERS for r in responses)
        assert stored_messages(abc) == []

    def test_logged_out_is_refused(
        self, abc: ConsultancyContext, students: dict[str, Student]
    ) -> None:
        logged_out = abc.new_client()

        responses = [logged_out.get(BULK_URL), logged_out.post(BULK_URL, {}, format="json")]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2

    def test_switched_off_it_is_not_found(
        self, abc: ConsultancyContext, set_up: None, students: dict[str, Student]
    ) -> None:
        switch_modules(abc.consultancy, {Module.BATCHES})

        responses = [send_bulk(abc, sms(students={})), abc.client.get(BULK_URL)]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        assert stored_messages(abc) == []

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_the_methods_needed_are_allowed(
        self, abc: ConsultancyContext, set_up: None, method: str
    ) -> None:
        response = getattr(abc.client, method)(BULK_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logs_counts_and_ids_only(
        self,
        abc: ConsultancyContext,
        set_up: None,
        students: dict[str, Student],
        json_logs: JsonLogs,
    ) -> None:
        send_bulk(abc, sms("Secret plans for Sita", students={"stage": "counselling"}))

        logged = json_logs.text()
        assert "Bulk message sent" in logged
        assert "sent=2, failed=1" in logged
        assert "Secret plans" not in logged
        assert "9841000001" not in logged
        assert "Sita Gurung" not in logged
