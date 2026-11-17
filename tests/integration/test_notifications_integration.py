"""Task notifications: given a Task, a Task due today or overdue; the list, read and read all.

Spec 80; issue 22.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from notifications.models import Notification, NotificationKind
from notifications.serializers import ReadField
from notifications.services import NOTIFICATIONS_PER_PAGE
from staff.models import Role, Staff, StaffStatus
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    log_in,
    make_student,
    named_staff,
)
from tests.integration.test_tasks_integration import TASKS_URL, day, good_body, make_task

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

    from tasks.models import Task

URL = "/api/notifications/"
READ_ALL_URL = "/api/notifications/read-all/"
READ_MESSAGE = {"read": ["Say whether it's read."]}
UNKNOWN_FIELD = ["Unknown field."]


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def one_url(notification: Notification | int) -> str:
    """One notification."""
    pk = notification if isinstance(notification, int) else notification.pk
    return f"/api/notifications/{pk}/"


def bell(office: ConsultancyContext, **query: str) -> dict[str, object]:
    """What the bell gets for whoever is logged in."""
    response = office.client.get(URL, query)
    assert response.status_code == status.HTTP_200_OK
    body: dict[str, object] = response.json()
    return body


def results(office: ConsultancyContext, **query: str) -> list[dict[str, Any]]:
    """The listed notifications, newest first."""
    items = bell(office, **query)["results"]
    assert isinstance(items, list)
    return items


def kinds(office: ConsultancyContext) -> list[tuple[str, str]]:
    """(kind, Task title) of each listed notification, newest first."""
    return [(item["kind"], item["task"]["title"]) for item in results(office)]


def stored(office: ConsultancyContext) -> list[Notification]:
    """Every stored notification, oldest first."""
    with tenant_context(office.consultancy):
        return list(Notification.objects.order_by("pk"))


def notify(office: ConsultancyContext, task: Task, kind: NotificationKind) -> Notification:
    """Store a notification directly."""
    with tenant_context(office.consultancy):
        return Notification.objects.create(recipient=task.assignee, task=task, kind=kind)


def mark(
    office: ConsultancyContext, notification: Notification | int, body: object
) -> _MonkeyPatchedResponse:
    """PATCH one notification."""
    return office.client.patch(one_url(notification), body, format="json")


def read_flags(office: ConsultancyContext) -> list[bool]:
    """Whether each stored notification is read, oldest first."""
    return [item.read_at is not None for item in stored(office)]


class TestGivenATask:
    def test_someone_given_a_task_is_told_who_gave_it(self, abc: ConsultancyContext) -> None:
        desk = named_staff(abc, "Ramesh KC", Role.FRONT_DESK)
        counsellor = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, counsellor, name="Manisha Rai")
        log_in(abc, desk)
        abc.client.post(TASKS_URL, good_body(counsellor, student=student.pk), format="json")
        log_in(abc, counsellor)

        body = bell(abc)

        [notification] = stored(abc)
        assert body == {
            "count": 1,
            "page": 1,
            "page_size": NOTIFICATIONS_PER_PAGE,
            "unread_count": 1,
            "results": [
                {
                    "id": notification.pk,
                    "kind": "assigned",
                    "created_at": notification.created_at.isoformat(),
                    "is_read": False,
                    "task": {
                        "id": notification.task_id,
                        "title": "Call Sita about her IELTS score",
                        "due_on": day(1).isoformat(),
                    },
                    "by": {"id": desk.pk, "name": "Ramesh KC"},
                    "student": {"id": student.pk, "name": "Manisha Rai"},
                    "transfer_request": None,
                    "bin": None,
                    "work_from_home": None,
                }
            ],
        }

    def test_a_task_you_give_yourself_tells_nobody(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)

        abc.client.post(TASKS_URL, good_body(me), format="json")

        assert stored(abc) == []
        assert bell(abc)["unread_count"] == 0

    def test_the_giver_is_not_told(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        counsellor = named_staff(abc, "Bikash Thapa")

        abc.client.post(TASKS_URL, good_body(counsellor), format="json")

        assert bell(abc)["count"] == 0
        assert [item.recipient_id for item in stored(abc)] == [counsellor.pk]

    def test_a_refused_task_tells_nobody(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        counsellor = named_staff(abc, "Bikash Thapa")

        abc.client.post(TASKS_URL, good_body(counsellor, title=""), format="json")

        assert stored(abc) == []

    def test_a_teacher_never_sees_the_student(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        teacher = named_staff(abc, "Sunita Shrestha", Role.TEACHER)
        student = make_student(abc)
        abc.client.post(TASKS_URL, good_body(teacher, student=student.pk), format="json")
        log_in(abc, teacher)

        [item] = results(abc)

        assert item["student"] is None
        assert item["by"] == {"id": admin.pk, "name": admin.name}

    @pytest.mark.parametrize("title", INJECTION_STRINGS)
    def test_typed_titles_come_back_as_plain_text(
        self, abc: ConsultancyContext, title: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        counsellor = named_staff(abc, "Bikash Thapa")
        abc.client.post(TASKS_URL, good_body(counsellor, title=title), format="json")
        log_in(abc, counsellor)

        assert kinds(abc) == [("assigned", title)]


class TestFallingDue:
    def test_a_task_due_today_tells_its_assignee_once(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        make_task(abc, me, title="Collect bank statement", due_on=day(0))

        first = kinds(abc)
        again = kinds(abc)

        assert first == again == [("due_today", "Collect bank statement")]
        assert len(stored(abc)) == 1
        assert bell(abc)["unread_count"] == 1

    def test_an_overdue_task_says_so(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        make_task(abc, me, title="Call Sita", due_on=day(-2))

        assert kinds(abc) == [("overdue", "Call Sita")]

    def test_still_open_the_day_after_it_was_due_it_says_overdue_too(
        self, abc: ConsultancyContext
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        task = make_task(abc, me, title="Call Sita", due_on=day(-1))
        notify(abc, task, NotificationKind.DUE_TODAY)

        listed = kinds(abc)

        assert sorted(listed) == [("due_today", "Call Sita"), ("overdue", "Call Sita")]
        assert listed[0] == ("overdue", "Call Sita")

    def test_done_and_later_tasks_say_nothing(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        make_task(abc, me, due_on=day(1))
        make_task(abc, me, due_on=day(-1), is_done=True, done_at=timezone.now())
        make_task(abc, me, due_on=day(0), is_done=True, done_at=timezone.now())

        assert kinds(abc) == []

    def test_only_the_assignee_is_told(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.ADMIN)
        counsellor = named_staff(abc, "Bikash Thapa")
        make_task(abc, counsellor, by=me, due_on=day(0))

        assert kinds(abc) == []
        log_in(abc, counsellor)
        assert kinds(abc) == [("due_today", "Collect bank statement")]

    def test_due_notifications_never_name_the_giver(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        make_task(abc, me, by=named_staff(abc, "Ramesh KC", Role.FRONT_DESK), due_on=day(0))

        [item] = results(abc)

        assert item["by"] is None


class TestTheList:
    def test_newest_first_and_only_mine(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = named_staff(abc, "Bikash Thapa")
        older = notify(abc, make_task(abc, me, title="Older"), NotificationKind.ASSIGNED)
        notify(abc, make_task(abc, me, title="Newer"), NotificationKind.ASSIGNED)
        notify(abc, make_task(abc, other, title="Not mine"), NotificationKind.ASSIGNED)
        with tenant_context(abc.consultancy):
            Notification.objects.filter(pk=older.pk).update(
                created_at=timezone.now() - timedelta(hours=1)
            )

        assert kinds(abc) == [("assigned", "Newer"), ("assigned", "Older")]

    def test_pages(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        for number in range(NOTIFICATIONS_PER_PAGE + 1):
            notify(abc, make_task(abc, me, title=f"Task {number}"), NotificationKind.ASSIGNED)

        second = bell(abc, page="2")

        assert second["count"] == NOTIFICATIONS_PER_PAGE + 1
        assert second["unread_count"] == NOTIFICATIONS_PER_PAGE + 1
        assert len(results(abc, page="2")) == 1
        assert abc.client.get(URL, {"page": "3"}).status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        "query", [{"page": "0"}, {"page": "x"}, {"page": "1.5"}, {"unread": "1"}]
    )
    def test_a_bad_query_is_refused(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        response = abc.client.get(URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestReading:
    def test_marking_one_read_and_unread_again(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        first = notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)
        notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)

        read = mark(abc, first, {"read": True})

        assert read.status_code == status.HTTP_200_OK
        assert read.json()["is_read"] is True
        assert bell(abc)["unread_count"] == 1
        assert read_flags(abc) == [True, False]
        mark(abc, first, {"read": False})
        assert read_flags(abc) == [False, False]

    def test_reading_twice_keeps_when_it_was_first_read(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        item = notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)
        mark(abc, item, {"read": True})
        [first] = stored(abc)

        mark(abc, item, {"read": True})

        assert stored(abc)[0].read_at == first.read_at

    def test_read_all_marks_only_mine(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        other = named_staff(abc, "Bikash Thapa")
        notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)
        make_task(abc, me, due_on=day(0))
        notify(abc, make_task(abc, other), NotificationKind.ASSIGNED)
        bell(abc)

        response = abc.client.post(READ_ALL_URL, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"unread_count": 0}
        assert bell(abc)["unread_count"] == 0
        assert read_flags(abc) == [True, False, True]

    def test_someone_elses_is_not_found_and_left_alone(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)
        other = named_staff(abc, "Bikash Thapa")
        theirs = notify(abc, make_task(abc, other), NotificationKind.ASSIGNED)

        response = mark(abc, theirs, {"read": True})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert read_flags(abc) == [False]

    def test_an_unknown_one_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.COUNSELLOR)

        assert mark(abc, 999_999, {"read": True}).status_code == status.HTTP_404_NOT_FOUND


class TestRefusedInput:
    @pytest.mark.parametrize("read", ["true", 1, 0, None, [], {"x": 1}, "<script>"])
    def test_read_must_be_a_json_boolean(self, abc: ConsultancyContext, read: object) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        item = notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)

        response = mark(abc, item, {"read": read})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == READ_MESSAGE
        assert read_flags(abc) == [False]

    def test_read_is_required(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        item = notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)

        assert mark(abc, item, {}).json() == READ_MESSAGE

    @pytest.mark.parametrize("field", ["kind", "recipient", "task", "read_at", "<b>x</b>"])
    def test_nothing_else_can_change(self, abc: ConsultancyContext, field: str) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        item = notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)

        response = mark(abc, item, {"read": True, field: "overdue"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": UNKNOWN_FIELD}
        assert read_flags(abc) == [False]
        assert stored(abc)[0].kind == NotificationKind.ASSIGNED

    @pytest.mark.parametrize("body", [[{"read": True}], "read", 1])
    def test_a_body_that_is_not_an_object_is_refused(
        self, abc: ConsultancyContext, body: object
    ) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        item = notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)

        assert mark(abc, item, body).status_code == status.HTTP_400_BAD_REQUEST
        assert abc.client.post(READ_ALL_URL, body, format="json").status_code == 400
        assert read_flags(abc) == [False]

    def test_read_all_takes_no_fields(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)

        response = abc.client.post(READ_ALL_URL, {"staff": 5}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": UNKNOWN_FIELD}
        assert read_flags(abc) == [False]

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        item = notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)

        patched = abc.client.patch(one_url(item), "read=true", content_type="text/plain")
        posted = abc.client.post(READ_ALL_URL, {})

        assert patched.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        assert posted.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        assert read_flags(abc) == [False]


class TestAccess:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_every_role_has_notifications(self, abc: ConsultancyContext, role: Role) -> None:
        me = abc.log_in_as(role)
        item = notify(abc, make_task(abc, me, due_on=day(0)), NotificationKind.ASSIGNED)

        assert [kind for kind, _ in kinds(abc)] == ["due_today", "assigned"]
        assert mark(abc, item, {"read": True}).status_code == status.HTTP_200_OK
        assert abc.client.post(READ_ALL_URL, {}, format="json").status_code == 200

    def test_logged_out_is_refused_everywhere(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        item = notify(abc, make_task(abc, staff), NotificationKind.ASSIGNED)
        client = abc.new_client()

        responses = [
            client.get(URL),
            client.patch(one_url(item), {"read": True}, format="json"),
            client.post(READ_ALL_URL, {}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_401_UNAUTHORIZED] * 3
        assert read_flags(abc) == [False]

    def test_a_deactivated_staff_member_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=me.pk).update(status=StaffStatus.DEACTIVATED)

        response = abc.client.post(READ_ALL_URL, {}, format="json")

        assert response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
        assert read_flags(abc) == [False]

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_the_list_answers_only_get(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("method", ["get", "put", "post", "delete"])
    def test_one_answers_only_patch(self, abc: ConsultancyContext, method: str) -> None:
        me = abc.log_in_as(Role.ADMIN)
        item = notify(abc, make_task(abc, me), NotificationKind.ASSIGNED)

        response = getattr(abc.client, method)(one_url(item), {"read": True}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert len(stored(abc)) == 1

    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_read_all_answers_only_post(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(READ_ALL_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_not_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        item = notify(abc, make_task(abc, staff), NotificationKind.ASSIGNED)
        platform_site.log_in()

        responses = [
            platform_site.client.get(URL),
            platform_site.client.patch(one_url(item), {"read": True}, format="json"),
            platform_site.client.post(READ_ALL_URL, {}, format="json"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3
        assert read_flags(abc) == [False]

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        me = abc.log_in_as(Role.ADMIN)
        other = named_staff(abc, "Bikash Thapa")
        abc.client.post(TASKS_URL, good_body(other, title="Secret task title"), format="json")
        [item] = stored(abc)
        log_in(abc, other)
        mark(abc, item, {"read": True})

        json_logs.only(
            f"Notification created: notification_id={item.pk}, kind=assigned, "
            f"task_id={item.task_id}, staff_id={other.pk}"
        )
        json_logs.only(f"Notification marked read: notification_id={item.pk}, staff_id={other.pk}")
        assert "Secret task" not in json_logs.text()
        assert "Bikash" not in json_logs.text()
        assert me.name not in json_logs.text()


class TestModel:
    def test_a_notification_reads_as_its_kind_and_task(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        task = make_task(abc, staff)

        assert str(notify(abc, task, NotificationKind.OVERDUE)) == f"overdue: {task.pk}"

    def test_the_read_field_shows_its_value(self) -> None:
        assert ReadField().to_representation(value=True) is True
