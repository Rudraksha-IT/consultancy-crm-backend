"""Inbox: one-to-one messages between two Staff of the same Consultancy (spec 128-130; issue 86)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from inbox.models import InboxMessage
from platform_admin.office_tools import exported_models
from staff.models import Role, Staff, StaffStatus
from tests.conftest import (
    ConsultancyContext,
    ConsultancyFactory,
    JsonLogs,
    LogsAt,
    PlatformContext,
    use_access_token,
)
from tests.integration.test_consultancy_status_integration import set_status
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    named_staff,
)
from tests.support_access import open_as_platform_admin

INBOX_URL = "/api/inbox/"
CHECK_URL = "/api/inbox/check/"
PEOPLE_URL = "/api/inbox/people/"
TEXT_NEEDED = {"text": ["Write a message."]}
TOO_LONG = {"text": ["Ensure this field has no more than 2000 characters."]}
HAS_LEFT = {"detail": "This person has left the office. You can't write to them."}
PLATFORM_REFUSED = {
    "detail": "Inbox conversations are private to the two people. Platform Admins can't read them."
}
PRIVATE_TEXT = "Sita's visa interview is moved to Thursday 10am"


def conversation_url(person: Staff | int) -> str:
    """Where the conversation with `person` is read and written."""
    pk = person if isinstance(person, int) else person.pk
    return f"/api/inbox/{pk}/"


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


class Office:
    """ABC's people: two counsellors, an Admin, Front Desk and a Teacher."""

    def __init__(self, office: ConsultancyContext) -> None:
        self.office = office
        self.sita = named_staff(office, "Sita Karki")
        self.bikash = named_staff(office, "Bikash Thapa")
        self.admin = named_staff(office, "Nabin Adhikari", Role.ADMIN)
        self.desk = named_staff(office, "Pooja Shrestha", Role.FRONT_DESK)
        self.teacher = named_staff(office, "Ramesh Karki", Role.TEACHER)

    def as_(self, staff: Staff) -> ConsultancyContext:
        """Log the office's client in as `staff` and return the office."""
        log_in(self.office, staff)
        return self.office

    def send(self, by: Staff, to: Staff, text: str = "Hello") -> dict[str, Any]:
        """Send a message as `by` through the API and return the answer."""
        response = self.as_(by).client.post(conversation_url(to), {"text": text}, format="json")
        assert response.status_code == status.HTTP_201_CREATED, response.content
        answer: dict[str, Any] = response.json()
        return answer

    def set_status_of(self, staff: Staff, value: StaffStatus) -> None:
        """Change a Staff member's status straight in the database."""
        with tenant_context(self.office.consultancy):
            Staff.objects.filter(pk=staff.pk).update(status=value)

    def messages(self) -> list[InboxMessage]:
        """Every stored message, oldest first."""
        with tenant_context(self.office.consultancy):
            return list(InboxMessage.objects.order_by("pk"))


@pytest.fixture
def people(abc: ConsultancyContext) -> Office:
    """ABC with its people."""
    return Office(abc)


class TestSending:
    """Spec 128: anyone writes to anyone else in the office, whatever their Role."""

    def test_a_message_is_stored_and_answered(self, people: Office) -> None:
        answer = people.send(people.sita, people.bikash, "Can you take the 3pm visitor?")

        assert answer["text"] == "Can you take the 3pm visitor?"
        assert answer["from_me"] is True
        assert set(answer) == {"id", "text", "from_me", "sent_at"}
        [stored] = people.messages()
        assert stored.sender_id == people.sita.pk
        assert stored.recipient_id == people.bikash.pk
        assert stored.read_at is None

    @pytest.mark.parametrize(
        ("sender", "recipient"),
        [("teacher", "admin"), ("desk", "teacher"), ("admin", "desk"), ("sita", "teacher")],
    )
    def test_every_role_writes_to_every_role(
        self, people: Office, sender: str, recipient: str
    ) -> None:
        people.send(getattr(people, sender), getattr(people, recipient))

        assert len(people.messages()) == 1

    def test_spaces_around_the_text_are_trimmed_and_lines_kept(self, people: Office) -> None:
        answer = people.send(people.sita, people.bikash, "  Line one\nLine two  ")

        assert answer["text"] == "Line one\nLine two"

    def test_2000_characters_are_fine(self, people: Office) -> None:
        people.send(people.sita, people.bikash, "a" * 2000)

        assert len(people.messages()[0].text) == 2000

    def test_writing_to_yourself_is_refused(self, people: Office) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(people.sita), {"text": "Hi"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert people.messages() == []

    def test_an_unknown_person_is_not_found(self, people: Office) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(99999), {"text": "Hi"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_invited_person_who_has_not_joined_is_not_found(self, people: Office) -> None:
        people.set_status_of(people.bikash, StaffStatus.INVITED)

        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash), {"text": "Hi"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert people.messages() == []


class TestRefusedInput:
    """Plain text, 1 to 2,000 characters, nothing else."""

    @pytest.mark.parametrize("text", ["", "   ", "\n\n"])
    def test_an_empty_message_is_refused(self, people: Office, text: str) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash), {"text": text}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TEXT_NEEDED
        assert people.messages() == []

    def test_a_missing_text_is_refused(self, people: Office) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash), {}, format="json"
        )

        assert response.json() == TEXT_NEEDED

    def test_over_2000_characters_is_refused(self, people: Office) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash), {"text": "a" * 2001}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == TOO_LONG
        assert people.messages() == []

    @pytest.mark.parametrize("text", [5, True, None, ["Hi"], {"text": "Hi"}])
    def test_wrong_types_are_refused(self, people: Office, text: object) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash), {"text": text}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert people.messages() == []

    def test_unknown_fields_are_refused_and_never_stored(self, people: Office) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash),
            {"text": "Hi", "sender": people.admin.pk, "read_at": "2026-01-01T00:00:00Z"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert people.messages() == []

    def test_control_characters_are_refused(self, people: Office) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash), {"text": "Hi\x00there"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert people.messages() == []

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_text_is_kept_as_text(self, people: Office, text: str) -> None:
        people.send(people.sita, people.bikash, text)

        thread = people.as_(people.bikash).client.get(conversation_url(people.sita)).json()

        assert thread["messages"][0]["text"] == text

    def test_a_form_body_is_refused(self, people: Office) -> None:
        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash), {"text": "Hi"}
        )

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_messages_cant_be_edited_or_deleted(self, people: Office, method: str) -> None:
        people.send(people.sita, people.bikash)
        client = people.as_(people.sita).client

        response = getattr(client, method)(conversation_url(people.bikash), format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert len(people.messages()) == 1

    @pytest.mark.parametrize("url", [INBOX_URL, CHECK_URL, PEOPLE_URL])
    def test_the_lists_are_read_only(self, people: Office, url: str) -> None:
        response = people.as_(people.sita).client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestConversations:
    """Spec 128: my conversations, newest first, each with whether it has unread messages."""

    def test_conversations_are_listed_newest_first(self, people: Office) -> None:
        people.send(people.admin, people.sita, "First")
        people.send(people.desk, people.sita, "Second")
        people.send(people.sita, people.admin, "Third")

        answer = people.as_(people.sita).client.get(INBOX_URL).json()

        assert [c["person"]["name"] for c in answer["conversations"]] == [
            "Nabin Adhikari",
            "Pooja Shrestha",
        ]
        latest = answer["conversations"][0]
        assert latest["last"]["text"] == "Third"
        assert latest["last"]["from_me"] is True
        assert latest["unread"] is True  # "First" from Nabin is still unread
        assert answer["conversations"][1]["unread"] is True

    def test_each_person_is_described(self, people: Office) -> None:
        people.send(people.admin, people.sita)

        [conversation] = people.as_(people.sita).client.get(INBOX_URL).json()["conversations"]

        assert conversation["person"] == {
            "id": people.admin.pk,
            "name": "Nabin Adhikari",
            "job_title": "",
            "has_left": False,
        }

    def test_a_long_last_message_is_cut_short_in_the_list(self, people: Office) -> None:
        people.send(people.admin, people.sita, "a" * 2000)

        [conversation] = people.as_(people.sita).client.get(INBOX_URL).json()["conversations"]

        assert len(conversation["last"]["text"]) <= 120

    def test_nobody_has_conversations_at_first(self, people: Office) -> None:
        answer = people.as_(people.sita).client.get(INBOX_URL).json()

        assert answer == {"conversations": []}

    def test_opening_a_conversation_shows_it_oldest_first_and_marks_it_read(
        self, people: Office
    ) -> None:
        people.send(people.admin, people.sita, "Can you call Anjali's father?")
        people.send(people.sita, people.admin, "Yes, after lunch.")
        people.send(people.admin, people.sita, "Thanks.")

        thread = people.as_(people.sita).client.get(conversation_url(people.admin)).json()

        assert [(m["text"], m["from_me"]) for m in thread["messages"]] == [
            ("Can you call Anjali's father?", False),
            ("Yes, after lunch.", True),
            ("Thanks.", False),
        ]
        assert thread["person"]["name"] == "Nabin Adhikari"
        assert thread["can_write"] is True
        assert thread["more"] is False
        assert people.as_(people.sita).client.get(CHECK_URL).json()["unread"] == 0

    def test_opening_marks_only_what_was_sent_to_me(self, people: Office) -> None:
        people.send(people.sita, people.admin, "For Nabin")

        people.as_(people.sita).client.get(conversation_url(people.admin))

        assert people.messages()[0].read_at is None

    def test_a_conversation_never_started_is_empty_and_writable(self, people: Office) -> None:
        thread = people.as_(people.sita).client.get(conversation_url(people.teacher)).json()

        assert thread["messages"] == []
        assert thread["can_write"] is True

    def test_a_long_conversation_shows_the_newest_200(self, people: Office) -> None:
        now = timezone.now()
        with tenant_context(people.office.consultancy):
            InboxMessage.objects.bulk_create(
                InboxMessage(
                    sender=people.admin,
                    recipient=people.sita,
                    text=f"Message {n}",
                    sent_at=now + timedelta(seconds=n),
                )
                for n in range(205)
            )

        thread = people.as_(people.sita).client.get(conversation_url(people.admin)).json()

        assert len(thread["messages"]) == 200
        assert thread["messages"][0]["text"] == "Message 5"
        assert thread["messages"][-1]["text"] == "Message 204"
        assert thread["more"] is True

    def test_an_unknown_or_own_conversation_is_not_found(self, people: Office) -> None:
        client = people.as_(people.sita).client

        assert client.get(conversation_url(99999)).status_code == status.HTTP_404_NOT_FOUND
        assert client.get(conversation_url(people.sita)).status_code == status.HTTP_404_NOT_FOUND

    def test_logged_out_is_refused(self, people: Office) -> None:
        client = people.office.new_client()

        for url in (INBOX_URL, CHECK_URL, PEOPLE_URL, conversation_url(people.sita)):
            assert client.get(url).status_code == status.HTTP_401_UNAUTHORIZED


class TestCheck:
    """One small request: how many conversations have unread messages, and the newest id."""

    def test_unread_counts_conversations_not_messages(self, people: Office) -> None:
        people.send(people.admin, people.sita, "One")
        people.send(people.admin, people.sita, "Two")
        people.send(people.desk, people.sita, "Three")

        answer = people.as_(people.sita).client.get(CHECK_URL).json()

        assert answer["unread"] == 2
        assert answer["latest"] == people.messages()[-1].pk

    def test_my_own_messages_are_not_unread_for_me(self, people: Office) -> None:
        people.send(people.sita, people.admin)

        answer = people.as_(people.sita).client.get(CHECK_URL).json()

        assert answer == {"unread": 0, "latest": people.messages()[0].pk}

    def test_nothing_yet(self, people: Office) -> None:
        assert people.as_(people.sita).client.get(CHECK_URL).json() == {
            "unread": 0,
            "latest": None,
        }

    def test_it_never_shows_message_text(self, people: Office) -> None:
        people.send(people.admin, people.sita, PRIVATE_TEXT)

        response = people.as_(people.sita).client.get(CHECK_URL)

        assert PRIVATE_TEXT not in response.content.decode()

    def test_checking_is_a_small_number_of_queries(
        self,
        people: Office,
        django_assert_max_num_queries: Any,  # noqa: ANN401
    ) -> None:
        for sender in (people.admin, people.desk, people.teacher):
            people.send(sender, people.sita)
        client = people.as_(people.sita).client
        client.get(CHECK_URL)  # warm up anything cached per process

        with django_assert_max_num_queries(6):
            client.get(CHECK_URL)


class TestPrivacy:
    """Spec 129: only the two people can read a conversation."""

    def test_a_third_person_sees_nothing_of_it(self, people: Office) -> None:
        people.send(people.sita, people.bikash, PRIVATE_TEXT)

        client = people.as_(people.desk).client

        assert client.get(INBOX_URL).json() == {"conversations": []}
        assert client.get(CHECK_URL).json() == {"unread": 0, "latest": None}
        thread = client.get(conversation_url(people.sita)).json()
        assert thread["messages"] == []
        assert PRIVATE_TEXT not in str(thread)

    def test_an_admin_cant_read_someone_elses_conversation(self, people: Office) -> None:
        people.send(people.sita, people.bikash, PRIVATE_TEXT)

        client = people.as_(people.admin).client

        assert client.get(INBOX_URL).json() == {"conversations": []}
        for person in (people.sita, people.bikash):
            assert PRIVATE_TEXT not in client.get(conversation_url(person)).content.decode()

    def test_a_third_person_opening_it_marks_nothing_read(self, people: Office) -> None:
        people.send(people.sita, people.bikash)

        people.as_(people.admin).client.get(conversation_url(people.sita))

        assert people.as_(people.bikash).client.get(CHECK_URL).json()["unread"] == 1

    @pytest.mark.parametrize(
        "url", [INBOX_URL, CHECK_URL, PEOPLE_URL, conversation_url(1), conversation_url(2)]
    )
    def test_a_platform_admin_in_support_access_is_refused(
        self, people: Office, platform_site: PlatformContext, url: str
    ) -> None:
        people.send(people.sita, people.bikash, PRIVATE_TEXT)
        visit = open_as_platform_admin(platform_site, people.office)

        response = visit.client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == PLATFORM_REFUSED

    def test_a_platform_admin_cant_write(
        self, people: Office, platform_site: PlatformContext
    ) -> None:
        visit = open_as_platform_admin(platform_site, people.office)

        response = visit.client.post(conversation_url(people.sita), {"text": "Hi"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert people.messages() == []

    def test_messages_are_left_out_of_the_office_data_export(self) -> None:
        assert InboxMessage not in list(exported_models())


class TestPeople:
    """The "New message" list: everyone active in the office, except me."""

    def test_everyone_active_but_me_by_name(self, people: Office) -> None:
        people.set_status_of(people.bikash, StaffStatus.DEACTIVATED)
        people.set_status_of(people.teacher, StaffStatus.INVITED)

        answer = people.as_(people.sita).client.get(PEOPLE_URL).json()

        assert [p["name"] for p in answer["people"]] == ["Nabin Adhikari", "Pooja Shrestha"]
        assert set(answer["people"][0]) == {"id", "name", "job_title"}


class TestDeactivatedStaff:
    """Spec 130: someone who left stays readable, but can't be written to."""

    def test_their_conversation_stays_readable_but_not_writable(self, people: Office) -> None:
        people.send(people.bikash, people.sita, "Handing over Rohan's notes.")
        people.set_status_of(people.bikash, StaffStatus.DEACTIVATED)
        client = people.as_(people.sita).client

        [conversation] = client.get(INBOX_URL).json()["conversations"]
        thread = client.get(conversation_url(people.bikash)).json()

        assert conversation["person"]["has_left"] is True
        assert thread["messages"][0]["text"] == "Handing over Rohan's notes."
        assert thread["can_write"] is False

    def test_writing_to_them_is_refused(self, people: Office) -> None:
        people.set_status_of(people.bikash, StaffStatus.DEACTIVATED)

        response = people.as_(people.sita).client.post(
            conversation_url(people.bikash), {"text": "Hi"}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == HAS_LEFT
        assert people.messages() == []

    def test_a_deactivated_person_cant_use_the_inbox(self, people: Office) -> None:
        people.send(people.sita, people.bikash)
        tokens = people.office.tokens_for(people.bikash)
        people.set_status_of(people.bikash, StaffStatus.DEACTIVATED)
        client = people.office.new_client()
        use_access_token(client, tokens.access)

        assert client.get(INBOX_URL).status_code == status.HTTP_401_UNAUTHORIZED


class TestReadOnlyOffice:
    """Spec 130: Past due and Suspended offices can read but not send."""

    @pytest.mark.parametrize("value", ["past_due", "suspended"])
    def test_reading_works_and_sending_is_refused(self, people: Office, value: str) -> None:
        people.send(people.admin, people.sita, "Before the office was limited")
        set_status(people.office, value)
        client = people.as_(people.sita).client

        assert client.get(INBOX_URL).status_code == status.HTTP_200_OK
        assert client.get(CHECK_URL).json()["unread"] == 1
        thread = client.get(conversation_url(people.admin))
        assert thread.status_code == status.HTTP_200_OK
        response = client.post(conversation_url(people.admin), {"text": "Hi"}, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert len(people.messages()) == 1


class TestLogs:
    """Message text is never written to the logs, at any level."""

    def test_sending_logs_who_and_never_what(self, people: Office, json_logs: JsonLogs) -> None:
        people.send(people.sita, people.bikash, PRIVATE_TEXT)

        sent = json_logs.with_message("Inbox message sent")
        assert len(sent) == 1
        assert f"from_staff_id={people.sita.pk}" in sent[0]["message"]
        assert PRIVATE_TEXT not in json_logs.text()

    def test_not_even_at_debug_or_on_refusal(self, people: Office, json_logs_at: LogsAt) -> None:
        logs = json_logs_at("DEBUG")
        people.send(people.sita, people.bikash, PRIVATE_TEXT)
        people.as_(people.bikash).client.get(conversation_url(people.sita))
        people.as_(people.bikash).client.get(INBOX_URL)
        people.as_(people.sita).client.post(
            conversation_url(people.bikash), {"text": PRIVATE_TEXT + "x" * 2000}, format="json"
        )

        assert PRIVATE_TEXT not in logs.text()
