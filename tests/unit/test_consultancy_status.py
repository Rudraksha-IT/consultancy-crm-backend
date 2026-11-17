"""The Consultancy Status rules: which moves are allowed and what each Status lets through."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory

from tenancy.middleware import ConsultancyAccessMiddleware
from tenancy.models import Consultancy
from tenancy.status import (
    ALLOWED_MOVES,
    ConsultancyStatus,
    can_move,
    decide_access,
    lets_support_access_in,
    restriction_message,
)

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=3)
EARLIER = NOW - timedelta(seconds=1)

READS = ("GET", "HEAD", "OPTIONS")
WRITES = ("POST", "PUT", "PATCH", "DELETE")
STUDENTS_URL = "/api/students/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
LOGOUT_URL = "/api/auth/logout/"
CONSULTANCY_URL = "/api/consultancy/"
READ_ONLY = (ConsultancyStatus.PAST_DUE, ConsultancyStatus.SUSPENDED)
EVERY_STATUS = tuple(ConsultancyStatus)


class TestMoves:
    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            (ConsultancyStatus.EVALUATION, ConsultancyStatus.ACTIVE),
            (ConsultancyStatus.EVALUATION, ConsultancyStatus.TERMINATED),
            (ConsultancyStatus.ACTIVE, ConsultancyStatus.ACTIVE),
            (ConsultancyStatus.ACTIVE, ConsultancyStatus.PAST_DUE),
            (ConsultancyStatus.ACTIVE, ConsultancyStatus.SUSPENDED),
            (ConsultancyStatus.ACTIVE, ConsultancyStatus.TERMINATED),
            (ConsultancyStatus.PAST_DUE, ConsultancyStatus.ACTIVE),
            (ConsultancyStatus.PAST_DUE, ConsultancyStatus.SUSPENDED),
            (ConsultancyStatus.PAST_DUE, ConsultancyStatus.TERMINATED),
            (ConsultancyStatus.SUSPENDED, ConsultancyStatus.ACTIVE),
            (ConsultancyStatus.SUSPENDED, ConsultancyStatus.PAST_DUE),
            (ConsultancyStatus.SUSPENDED, ConsultancyStatus.TERMINATED),
        ],
    )
    def test_allowed(self, from_status: ConsultancyStatus, to_status: ConsultancyStatus) -> None:
        assert can_move(from_status, to_status)

    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            (ConsultancyStatus.EVALUATION, ConsultancyStatus.EVALUATION),
            (ConsultancyStatus.EVALUATION, ConsultancyStatus.PAST_DUE),
            (ConsultancyStatus.EVALUATION, ConsultancyStatus.SUSPENDED),
            (ConsultancyStatus.ACTIVE, ConsultancyStatus.EVALUATION),
            (ConsultancyStatus.PAST_DUE, ConsultancyStatus.PAST_DUE),
            (ConsultancyStatus.PAST_DUE, ConsultancyStatus.EVALUATION),
            (ConsultancyStatus.SUSPENDED, ConsultancyStatus.SUSPENDED),
            (ConsultancyStatus.SUSPENDED, ConsultancyStatus.EVALUATION),
            *((ConsultancyStatus.TERMINATED, to_status) for to_status in ConsultancyStatus),
        ],
    )
    def test_refused(self, from_status: ConsultancyStatus, to_status: ConsultancyStatus) -> None:
        assert not can_move(from_status, to_status)

    def test_terminated_is_final(self) -> None:
        assert ALLOWED_MOVES[ConsultancyStatus.TERMINATED] == frozenset()

    def test_every_status_has_its_moves_listed(self) -> None:
        assert set(ALLOWED_MOVES) == set(ConsultancyStatus)


def access(
    status: ConsultancyStatus,
    method: str,
    path: str = STUDENTS_URL,
    ends_at: datetime | None = LATER,
) -> int | None:
    """Return the refusal's HTTP status, or None when the request may go through."""
    refusal = decide_access(status, ends_at, NOW, method, path)
    return None if refusal is None else refusal.http_status


class TestAccess:
    @pytest.mark.parametrize("method", READS + WRITES)
    def test_active_lets_everything_through(self, method: str) -> None:
        assert access(ConsultancyStatus.ACTIVE, method) is None

    @pytest.mark.parametrize("method", READS + WRITES)
    def test_evaluation_lets_everything_through_before_it_ends(self, method: str) -> None:
        assert access(ConsultancyStatus.EVALUATION, method) is None

    @pytest.mark.parametrize("method", READS + WRITES)
    @pytest.mark.parametrize("ends_at", [EARLIER, NOW, None])
    def test_evaluation_blocks_everything_once_ended(
        self, method: str, ends_at: datetime | None
    ) -> None:
        assert access(ConsultancyStatus.EVALUATION, method, ends_at=ends_at) == 403

    @pytest.mark.parametrize("path", [LOGIN_URL, REFRESH_URL])
    def test_an_ended_evaluation_refuses_logging_in(self, path: str) -> None:
        assert access(ConsultancyStatus.EVALUATION, "POST", path, EARLIER) == 403

    def test_an_ended_evaluation_still_lets_staff_log_out(self) -> None:
        assert access(ConsultancyStatus.EVALUATION, "POST", LOGOUT_URL, EARLIER) is None

    @pytest.mark.parametrize("path", [CONSULTANCY_URL, "/api/consultancy/logo/3/"])
    def test_an_ended_evaluation_still_shows_the_login_screen_who_it_is(self, path: str) -> None:
        assert access(ConsultancyStatus.EVALUATION, "GET", path, EARLIER) is None
        assert access(ConsultancyStatus.EVALUATION, "POST", path, EARLIER) == 403

    @pytest.mark.parametrize("status", READ_ONLY)
    @pytest.mark.parametrize("method", READS)
    def test_read_only_statuses_let_reads_through(
        self, status: ConsultancyStatus, method: str
    ) -> None:
        assert access(status, method) is None

    @pytest.mark.parametrize("status", READ_ONLY)
    @pytest.mark.parametrize("method", WRITES)
    def test_read_only_statuses_refuse_changes(
        self, status: ConsultancyStatus, method: str
    ) -> None:
        assert access(status, method) == 403

    @pytest.mark.parametrize("status", READ_ONLY)
    @pytest.mark.parametrize("path", [LOGIN_URL, REFRESH_URL, LOGOUT_URL])
    def test_read_only_statuses_still_let_staff_log_in_and_out(
        self, status: ConsultancyStatus, path: str
    ) -> None:
        assert access(status, "POST", path) is None

    @pytest.mark.parametrize("status", READ_ONLY)
    @pytest.mark.parametrize(
        "path",
        [
            "/api/auth/login/extra/",
            "/api/auth/login",
            "/api/auth/password-reset/",
            "/api/auth/join/complete/",
            "/api/auth/logout/../students/",
        ],
    )
    def test_only_the_exact_login_paths_are_let_through(
        self, status: ConsultancyStatus, path: str
    ) -> None:
        assert access(status, "POST", path) == 403

    @pytest.mark.parametrize("status", READ_ONLY)
    @pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
    def test_login_paths_only_let_a_post_through(
        self, status: ConsultancyStatus, method: str
    ) -> None:
        assert access(status, method, LOGIN_URL) == 403

    @pytest.mark.parametrize("method", READS + WRITES)
    def test_terminated_blocks_everything(self, method: str) -> None:
        assert access(ConsultancyStatus.TERMINATED, method) == 404
        assert access(ConsultancyStatus.TERMINATED, method, LOGOUT_URL) == 404

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_methods_are_compared_in_capitals(self, method: str) -> None:
        expected = None if method == "get" else 403
        assert access(ConsultancyStatus.SUSPENDED, method) == expected

    def test_a_refusal_says_why_and_names_the_status(self) -> None:
        refusal = decide_access(ConsultancyStatus.PAST_DUE, None, NOW, "POST", STUDENTS_URL)

        assert refusal is not None
        assert refusal.body() == {
            "detail": restriction_message(ConsultancyStatus.PAST_DUE, None, NOW),
            "status": "past_due",
        }


class TestRestrictionMessage:
    @pytest.mark.parametrize("status", [ConsultancyStatus.ACTIVE, ConsultancyStatus.EVALUATION])
    def test_nothing_to_say_when_staff_can_work(self, status: ConsultancyStatus) -> None:
        assert restriction_message(status, LATER, NOW) is None

    @pytest.mark.parametrize("status", READ_ONLY)
    def test_read_only_says_staff_can_look_but_not_change(self, status: ConsultancyStatus) -> None:
        message = restriction_message(status, None, NOW)

        assert message is not None
        assert "look things up but not make changes" in message
        assert "Admin" in message

    def test_past_due_and_suspended_say_different_things(self) -> None:
        assert restriction_message(ConsultancyStatus.PAST_DUE, None, NOW) != restriction_message(
            ConsultancyStatus.SUSPENDED, None, NOW
        )

    def test_an_ended_evaluation_says_the_trial_has_ended(self) -> None:
        message = restriction_message(ConsultancyStatus.EVALUATION, EARLIER, NOW)

        assert message is not None
        assert "trial has ended" in message

    def test_terminated_has_a_message_too(self) -> None:
        assert restriction_message(ConsultancyStatus.TERMINATED, None, NOW) is not None

    @pytest.mark.parametrize("status", EVERY_STATUS)
    def test_messages_are_short(self, status: ConsultancyStatus) -> None:
        message = restriction_message(status, EARLIER, NOW)

        assert message is None or len(message) <= 160


class TestAccessMiddleware:
    def test_a_terminated_consultancy_that_still_resolved_is_not_found(self) -> None:
        request = RequestFactory().get("/api/context/")
        request.tenant = Consultancy(name="ABC", schema_name="abcedu", status="terminated")  # type: ignore[attr-defined]
        middleware = ConsultancyAccessMiddleware(lambda _: HttpResponse())

        with pytest.raises(Http404):
            middleware(request)

    def test_a_request_without_a_consultancy_goes_through(self) -> None:
        request = RequestFactory().post("/api/platform/consultancies/")
        middleware = ConsultancyAccessMiddleware(lambda _: HttpResponse(status=201))

        assert middleware(request).status_code == 201


ENTER_PATH = "/api/support-access/enter/"


class TestSupportAccessGetsIn:
    @pytest.mark.parametrize(
        "status", [value for value in ConsultancyStatus if value != ConsultancyStatus.TERMINATED]
    )
    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
    def test_a_visit_gets_into_every_status_but_terminated(
        self, status: ConsultancyStatus, method: str
    ) -> None:
        assert lets_support_access_in(status, method, "/api/students/", is_support_access=True)

    @pytest.mark.parametrize("status", list(ConsultancyStatus))
    @pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
    def test_without_a_visit_the_status_decides(
        self, status: ConsultancyStatus, method: str
    ) -> None:
        assert not lets_support_access_in(status, method, "/api/students/", is_support_access=False)

    @pytest.mark.parametrize(
        "status", [value for value in ConsultancyStatus if value != ConsultancyStatus.TERMINATED]
    )
    def test_entering_a_visit_gets_in(self, status: ConsultancyStatus) -> None:
        assert lets_support_access_in(status, "post", ENTER_PATH, is_support_access=False)

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_only_post_enters(self, method: str) -> None:
        assert not lets_support_access_in(
            ConsultancyStatus.SUSPENDED, method, ENTER_PATH, is_support_access=False
        )

    @pytest.mark.parametrize("is_support_access", [True, False])
    @pytest.mark.parametrize("path", ["/api/students/", ENTER_PATH])
    def test_terminated_never(self, *, is_support_access: bool, path: str) -> None:
        assert not lets_support_access_in(
            ConsultancyStatus.TERMINATED, "POST", path, is_support_access=is_support_access
        )
