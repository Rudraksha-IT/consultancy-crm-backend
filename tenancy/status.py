"""The Consultancy Status rules (spec 13-13d): which moves are allowed, and what gets through.

Pure: no database, no clock. The middleware and the services call these.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# A new Consultancy's evaluation lasts this long unless a Platform Admin extends it (spec 13a).
EVALUATION_LENGTH = timedelta(days=14)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The only changes a read-only Consultancy still takes: logging in, staying logged in, logging out.
# The second step of a login (issue 62) is part of logging in.
_LOGIN_PATHS = frozenset(
    {
        "/api/auth/login/",
        "/api/auth/login/next/",
        "/api/auth/refresh/",
        "/api/auth/logout/",
        "/api/auth/two-factor/setup/",
        "/api/auth/two-factor/confirm/",
        "/api/auth/two-factor/verify/",
    }
)
_LOGOUT_PATH = "/api/auth/logout/"
# Where a Platform Admin enters a Support Access visit, which any Status but Terminated allows.
SUPPORT_ENTER_PATH = "/api/support-access/enter/"
# What the login screen reads to show which office it is, even when the office is blocked.
_LOGIN_SCREEN_PREFIX = "/api/consultancy/"

FORBIDDEN = 403
NOT_FOUND = 404

_ASK_ADMIN = "Ask your Admin to contact the CRM team."
_MESSAGES = {
    "past_due": (
        "Your office's account is past due, so you can look things up but not make changes. "
        + _ASK_ADMIN
    ),
    "suspended": (
        "Your office's account is suspended, so you can look things up but not make changes. "
        + _ASK_ADMIN
    ),
    "trial_ended": "Your office's trial has ended. " + _ASK_ADMIN,
    "terminated": "This office's account has been closed.",
}


class ConsultancyStatus(StrEnum):
    """Where a Consultancy is with us as a customer (GLOSSARY: Consultancy Status)."""

    EVALUATION = "evaluation"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


# Evaluation -> Active; Active, Past due and Suspended between each other (Active -> Active
# renews the paid-until date); anything -> Terminated, which is final.
ALLOWED_MOVES: dict[ConsultancyStatus, frozenset[ConsultancyStatus]] = {
    ConsultancyStatus.EVALUATION: frozenset(
        {ConsultancyStatus.ACTIVE, ConsultancyStatus.TERMINATED}
    ),
    ConsultancyStatus.ACTIVE: frozenset(
        {
            ConsultancyStatus.ACTIVE,
            ConsultancyStatus.PAST_DUE,
            ConsultancyStatus.SUSPENDED,
            ConsultancyStatus.TERMINATED,
        }
    ),
    ConsultancyStatus.PAST_DUE: frozenset(
        {ConsultancyStatus.ACTIVE, ConsultancyStatus.SUSPENDED, ConsultancyStatus.TERMINATED}
    ),
    ConsultancyStatus.SUSPENDED: frozenset(
        {ConsultancyStatus.ACTIVE, ConsultancyStatus.PAST_DUE, ConsultancyStatus.TERMINATED}
    ),
    ConsultancyStatus.TERMINATED: frozenset(),
}

READ_ONLY_STATUSES = frozenset({ConsultancyStatus.PAST_DUE, ConsultancyStatus.SUSPENDED})
# A reason is required to move into these (spec: Platform services).
REASON_REQUIRED = frozenset(
    {ConsultancyStatus.PAST_DUE, ConsultancyStatus.SUSPENDED, ConsultancyStatus.TERMINATED}
)


@dataclass(frozen=True)
class AccessRefusal:
    """Why a request to a Consultancy is refused, as the API answers it."""

    http_status: int
    status: ConsultancyStatus
    message: str

    def body(self) -> dict[str, str]:
        """Return the JSON answer: a short reason and the Status."""
        return {"detail": self.message, "status": self.status.value}


def can_move(from_status: ConsultancyStatus, to_status: ConsultancyStatus) -> bool:
    """Return whether a Consultancy may go from one Status to the other."""
    return to_status in ALLOWED_MOVES[from_status]


def has_evaluation_ended(evaluation_ends_at: datetime | None, now: datetime) -> bool:
    """Return whether an evaluation is over; one with no end date counts as over."""
    return evaluation_ends_at is None or now >= evaluation_ends_at


def restriction_message(
    status: ConsultancyStatus, evaluation_ends_at: datetime | None, now: datetime
) -> str | None:
    """Return what Staff are told when the Status limits them, or None when it doesn't."""
    if status == ConsultancyStatus.EVALUATION:
        return _MESSAGES["trial_ended"] if has_evaluation_ended(evaluation_ends_at, now) else None
    if status == ConsultancyStatus.ACTIVE:
        return None
    return _MESSAGES[status.value]


def lets_support_access_in(
    status: ConsultancyStatus, method: str, path: str, *, is_support_access: bool
) -> bool:
    """Return whether a Platform Admin's Support Access gets past the Status (issue 27).

    On a visit (`is_support_access`), or entering one, they may read and change any Consultancy
    that isn't Terminated, to help it; otherwise the Status decides (`decide_access`).
    """
    if status == ConsultancyStatus.TERMINATED:
        return False
    return is_support_access or (method.upper() == "POST" and path == SUPPORT_ENTER_PATH)


def decide_access(
    status: ConsultancyStatus,
    evaluation_ends_at: datetime | None,
    now: datetime,
    method: str,
    path: str,
) -> AccessRefusal | None:
    """Return why a request is refused under the Consultancy's Status, or None to let it in."""
    method = method.upper()
    if status == ConsultancyStatus.TERMINATED:
        return _refusal(NOT_FOUND, status, evaluation_ends_at, now)
    if status in READ_ONLY_STATUSES:
        if method in _SAFE_METHODS or (method == "POST" and path in _LOGIN_PATHS):
            return None
        return _refusal(FORBIDDEN, status, evaluation_ends_at, now)
    if status == ConsultancyStatus.EVALUATION and has_evaluation_ended(evaluation_ends_at, now):
        return _ended_evaluation_access(status, evaluation_ends_at, now, method, path)
    return None


def _ended_evaluation_access(
    status: ConsultancyStatus,
    evaluation_ends_at: datetime | None,
    now: datetime,
    method: str,
    path: str,
) -> AccessRefusal | None:
    if method in _SAFE_METHODS and path.startswith(_LOGIN_SCREEN_PREFIX):
        return None
    if method == "POST" and path == _LOGOUT_PATH:
        return None
    return _refusal(FORBIDDEN, status, evaluation_ends_at, now)


def _refusal(
    http_status: int,
    status: ConsultancyStatus,
    evaluation_ends_at: datetime | None,
    now: datetime,
) -> AccessRefusal:
    message = restriction_message(status, evaluation_ends_at, now) or ""
    return AccessRefusal(http_status, status, message)
