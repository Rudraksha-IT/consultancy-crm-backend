"""Email to many Students at once: the Students are chosen first (ADR 0023).

Spec (2026-11-18) story 43 (Plain email only for now) and 51; issue 04. A class or a Student
list only pre-ticks Students (GET); the email goes to the Students chosen (POST), each their
own copy. Sent email is read from Django's test mailbox.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
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
from tests.integration.test_batches_integration import make_batch
from tests.integration.test_communication_integration import (
    CONTACT_EMAIL,
    NO_CONTACT_EMAIL,
    NO_EMAIL_ADDRESS,
    SEND_FAILED,
    outbox,
    set_contact_email,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings
    from rest_framework.response import _MonkeyPatchedResponse

BULK_URL = "/api/messages/bulk/"
NOT_FOR_TEACHERS = {"detail": "Teachers can't see Students."}
NOT_FOR_FRONT_DESK = {"detail": "Only Admins, counsellors and teachers can see classes."}
CHOOSE_SOMEONE = {"students": ["Choose at least one Student."]}
NOT_FOUND = {"students": ["Some of these Students can't be found."]}
LOST = {"students": ["Lost Students can't be chosen."]}
TOO_SLOW = "Not sent: sending took too long. Send again to this Student."


def send_bulk(office: ConsultancyContext, body: object) -> _MonkeyPatchedResponse:
    """POST one email to many Students, as given."""
    return office.client.post(BULK_URL, body, format="json")


def bulk(
    *students: Student | str, subject: str = "Office closed", body: str = ""
) -> dict[str, object]:
    """An email to the Students chosen, by UUID (or any text, to try bad ones)."""
    ids = [s if isinstance(s, str) else str(s.public_id) for s in students]
    text = body or "Dear Student,\n\nThe office is closed on Friday."
    return {"subject": subject, "body": text, "students": ids}


def stored_messages(office: ConsultancyContext) -> list[Message]:
    """Every stored email, oldest first, with its Student."""
    with tenant_context(office.consultancy):
        return list(Message.objects.select_related("student").order_by("pk"))


def chosen(answer: dict[str, Any]) -> list[str]:
    """The names of the Students a pre-tick answer gives."""
    return [student["name"] for student in answer["students"]]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Email and classes on and a contact email."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.COMMUNICATION, Module.BATCHES})
    set_contact_email(office)
    return office


@pytest.fixture
def admin(abc: ConsultancyContext) -> Staff:
    """The office's Admin, logged in."""
    staff = named_staff(abc, "Asha Admin", Role.ADMIN)
    log_in(abc, staff)
    return staff


@pytest.fixture
def counsellor(abc: ConsultancyContext) -> Staff:
    """A Counsellor."""
    return named_staff(abc, "Ram Counsellor", Role.COUNSELLOR)


@pytest.fixture
def teacher(abc: ConsultancyContext) -> Staff:
    """A Teacher."""
    return named_staff(abc, "Tara Teacher", Role.TEACHER)


@pytest.fixture
def students(abc: ConsultancyContext, counsellor: Staff) -> dict[str, Student]:
    """Ram's Students Sita and Gita (email), Hari (no email), an unassigned Lead and a Lost one."""
    return {
        "sita": make_student(
            abc, counsellor, Stage.COUNSELLING, name="Sita Gurung", email="sita@example.com"
        ),
        "hari": make_student(abc, counsellor, Stage.COUNSELLING, name="Hari Thapa"),
        "gita": make_student(
            abc, counsellor, Stage.COUNSELLING, name="Gita Rai", email="gita@example.com"
        ),
        "maya": make_student(abc, None, Stage.LEAD, name="Maya Lama", email="maya@example.com"),
        "lila": make_student(
            abc, counsellor, Stage.LOST, name="Lila Shrestha", email="lila@example.com"
        ),
    }


@pytest.fixture
def ielts(abc: ConsultancyContext, teacher: Staff, students: dict[str, Student]) -> int:
    """The IELTS Morning class, with Sita, Hari, Maya and Lila in it; returns its id."""
    members = tuple(students[name] for name in ("sita", "hari", "maya", "lila"))
    return make_batch(abc, teacher, members, name="IELTS Morning").pk


class TestSendingToTheStudentsChosen:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK])
    def test_each_student_chosen_gets_their_own_copy(
        self, abc: ConsultancyContext, students: dict[str, Student], role: Role
    ) -> None:
        sender = abc.log_in_as(role)

        response = send_bulk(abc, bulk(students["sita"], students["gita"], students["maya"]))

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"sent": 3, "failed": 0, "failed_messages": []}
        assert sorted(sent.to[0] for sent in outbox()) == [
            "gita@example.com",
            "maya@example.com",
            "sita@example.com",
        ]
        assert all(len(sent.to) == 1 and sent.cc == [] and sent.bcc == [] for sent in outbox())
        assert all(sent.reply_to == [CONTACT_EMAIL] for sent in outbox())
        assert all("Dear Student," in sent.body for sent in outbox())
        messages = stored_messages(abc)
        assert {m.sent_by_id for m in messages} == {sender.pk}
        assert {(m.channel, m.subject, m.status) for m in messages} == {
            ("email", "Office closed", "sent")
        }

    def test_a_student_without_an_address_fails_alone(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        response = send_bulk(abc, bulk(students["sita"], students["hari"]))

        answer = response.json()
        assert (answer["sent"], answer["failed"]) == (1, 1)
        [failed] = answer["failed_messages"]
        assert (failed["student"]["name"], failed["error"], failed["subject"]) == (
            "Hari Thapa",
            NO_EMAIL_ADDRESS,
            "Office closed",
        )
        assert [sent.to for sent in outbox()] == [["sita@example.com"]]

    def test_a_student_chosen_twice_gets_one_copy(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        response = send_bulk(abc, bulk(students["sita"], students["sita"]))

        assert response.json()["sent"] == 1
        assert len(outbox()) == 1

    def test_a_mail_server_that_fails_fails_each_without_its_words(
        self,
        abc: ConsultancyContext,
        admin: Staff,
        students: dict[str, Student],
        settings: Settings,
    ) -> None:
        settings.EMAIL_BACKEND = "tests.fakes.BrokenEmailBackend"

        response = send_bulk(abc, bulk(students["sita"], students["gita"]))

        answer = response.json()
        assert (answer["sent"], answer["failed"]) == (0, 2)
        assert {m["error"] for m in answer["failed_messages"]} == {SEND_FAILED}

    @override_settings(BULK_SEND_SECONDS=0)
    def test_students_not_reached_in_time_are_recorded_as_failed(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        response = send_bulk(abc, bulk(students["sita"], students["gita"]))

        assert response.json()["failed"] == 2
        assert {m.error for m in stored_messages(abc)} == {TOO_SLOW}
        assert outbox() == []

    def test_without_a_contact_email_nothing_is_sent_or_recorded(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        set_contact_email(abc, "")

        response = send_bulk(abc, bulk(students["sita"]))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NO_CONTACT_EMAIL
        assert stored_messages(abc) == []
        assert outbox() == []

    def test_injection_shaped_text_is_kept_as_typed(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        for text in INJECTION_STRINGS:
            send_bulk(abc, bulk(students["sita"], subject=text, body=text))

        assert [m.body for m in stored_messages(abc)] == INJECTION_STRINGS
        assert all("<script>" not in str(sent.alternatives[0][0]) for sent in outbox())

    def test_logs_counts_and_ids_only(
        self,
        abc: ConsultancyContext,
        admin: Staff,
        students: dict[str, Student],
        json_logs: JsonLogs,
    ) -> None:
        send_bulk(abc, bulk(students["sita"], students["hari"], subject="Secret subject"))

        [line] = json_logs.with_message("Bulk email sent")
        assert line["message"] == f"Bulk email sent: by_staff_id={admin.pk}, sent=1, failed=1"
        for private in ("Secret subject", "sita@example.com", "The office is closed"):
            assert private not in json_logs.text()


class TestRefusedBulkEmails:
    def _refused(self, abc: ConsultancyContext, body: object) -> dict[str, Any]:
        response = send_bulk(abc, body)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_messages(abc) == []
        assert outbox() == []
        answer: dict[str, Any] = response.json()
        return answer

    def test_unknown_fields_are_refused(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        for extra in ({"to": "x@evil.example"}, {"channel": "sms"}, {"batch": 1}):
            answer = self._refused(abc, {**bulk(students["sita"]), **extra})
            assert answer == {"detail": ["Unknown field."]}

    def test_nobody_chosen_is_refused(self, abc: ConsultancyContext, admin: Staff) -> None:
        assert self._refused(abc, bulk()) == CHOOSE_SOMEONE

    def test_a_lost_student_cant_be_chosen(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        assert self._refused(abc, bulk(students["sita"], students["lila"])) == LOST

    def test_an_unknown_student_is_refused(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        assert self._refused(abc, bulk(students["sita"], str(uuid.uuid4()))) == NOT_FOUND

    @override_settings(BULK_MAX_STUDENTS=2)
    def test_too_many_students_are_refused(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        answer = self._refused(abc, bulk(students["sita"], students["gita"], students["maya"]))

        assert answer == {"students": ["Email at most 2 Students at once."]}

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"body": "Hi", "students": [1]}, "subject"),
            ({"subject": "Hi", "students": [1]}, "body"),
            ({"subject": "Hi", "body": "Hi"}, "students"),
            ({"subject": "Two\nlines", "body": "Hi", "students": [1]}, "subject"),
            ({"subject": "x" * 151, "body": "Hi", "students": [1]}, "subject"),
            ({"subject": "Hi", "body": "x" * 10_001, "students": [1]}, "body"),
            ({"subject": "Hi", "body": "Hi", "students": "1"}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": 1}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": {"stage": "lead"}}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": ["1"]}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": [True]}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": [1.5]}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": [0]}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": [2**63]}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": [None]}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": ["not-a-uuid"]}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": [str(uuid.uuid4()).upper()]}, "students"),
            ({"subject": "Hi", "body": "Hi", "students": None}, "students"),
        ],
    )
    def test_wrong_input_is_refused(
        self,
        abc: ConsultancyContext,
        admin: Staff,
        students: dict[str, Student],
        body: dict[str, object],
        field: str,
    ) -> None:
        assert field in self._refused(abc, body)

    @pytest.mark.parametrize("body", [["Hi"], "Hi", 7, None])
    def test_a_body_that_isnt_an_object_is_refused(
        self, abc: ConsultancyContext, admin: Staff, body: object
    ) -> None:
        self._refused(abc, body)

    def test_a_form_body_is_refused(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        response = abc.client.post(BULK_URL, {"subject": "Hi", "body": "Hi"})

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class TestPreTicking:
    """A class or the Student list's filters only pre-tick the Students who can be chosen."""

    def test_a_class_pre_ticks_its_students_with_an_address_who_arent_lost(
        self, abc: ConsultancyContext, admin: Staff, ielts: int
    ) -> None:
        response = abc.client.get(BULK_URL, {"batch": ielts})

        assert response.status_code == status.HTTP_200_OK
        answer = response.json()
        assert chosen(answer) == ["Maya Lama", "Sita Gurung"]
        assert (answer["count"], answer["max"]) == (2, BULK_MAX_STUDENTS)
        assert set(answer["students"][0]) == {"id", "name", "email", "stage"}

    def test_the_student_lists_filters_pre_tick_too(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        answers = [
            abc.client.get(BULK_URL, {"stage": "counselling"}).json(),
            abc.client.get(BULK_URL, {"q": "maya"}).json(),
            abc.client.get(BULK_URL).json(),
        ]

        assert [chosen(answer) for answer in answers] == [
            ["Gita Rai", "Sita Gurung"],
            ["Maya Lama"],
            ["Gita Rai", "Maya Lama", "Sita Gurung"],
        ]

    def test_assigned_to_me_means_the_one_asking(
        self, abc: ConsultancyContext, counsellor: Staff, students: dict[str, Student]
    ) -> None:
        log_in(abc, counsellor)

        answer = abc.client.get(BULK_URL, {"counsellor": "me"}).json()

        assert chosen(answer) == ["Gita Rai", "Sita Gurung"]

    @override_settings(BULK_MAX_STUDENTS=2)
    def test_at_most_the_limit_is_pre_ticked_and_all_are_counted(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        answer = abc.client.get(BULK_URL).json()

        assert (len(answer["students"]), answer["count"], answer["max"]) == (2, 3, 2)

    def test_front_desk_cant_pre_tick_a_class(self, abc: ConsultancyContext, ielts: int) -> None:
        abc.log_in_as(Role.FRONT_DESK)

        response = abc.client.get(BULK_URL, {"batch": ielts})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == NOT_FOR_FRONT_DESK

    def test_an_unknown_class_is_not_found(self, abc: ConsultancyContext, admin: Staff) -> None:
        response = abc.client.get(BULK_URL, {"batch": 999_999})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_with_classes_switched_off_a_class_is_not_found_and_lists_still_work(
        self, abc: ConsultancyContext, admin: Staff, ielts: int
    ) -> None:
        switch_modules(abc.consultancy, {Module.COMMUNICATION})

        responses = [abc.client.get(BULK_URL, {"batch": ielts}), abc.client.get(BULK_URL)]

        assert [r.status_code for r in responses] == [
            status.HTTP_404_NOT_FOUND,
            status.HTTP_200_OK,
        ]

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
        self, abc: ConsultancyContext, admin: Staff, query: dict[str, str]
    ) -> None:
        response = abc.client.get(BULK_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_injection_shaped_search_finds_nobody(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        for text in INJECTION_STRINGS:
            answer = abc.client.get(BULK_URL, {"q": text[:100]}).json()
            assert answer["students"] == []


class TestBulkAccess:
    def test_teachers_cant_send_or_pre_tick(
        self, abc: ConsultancyContext, teacher: Staff, students: dict[str, Student]
    ) -> None:
        log_in(abc, teacher)

        responses = [abc.client.get(BULK_URL), send_bulk(abc, bulk(students["sita"]))]

        assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 2
        assert all(r.json() == NOT_FOR_TEACHERS for r in responses)
        assert outbox() == []

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        logged_out = abc.new_client()

        responses = [logged_out.get(BULK_URL), logged_out.post(BULK_URL, {}, format="json")]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 2

    def test_switched_off_it_is_not_found(
        self, abc: ConsultancyContext, admin: Staff, students: dict[str, Student]
    ) -> None:
        switch_modules(abc.consultancy, set())

        responses = [send_bulk(abc, bulk(students["sita"])), abc.client.get(BULK_URL)]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2
        assert len(mail.outbox) == 0

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_the_methods_needed_are_allowed(
        self, abc: ConsultancyContext, admin: Staff, method: str
    ) -> None:
        response = getattr(abc.client, method)(BULK_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
