"""Public services for two-step login (issue 61). Call them on the schema the person belongs to.

Built once for Platform Admins (61) and reused for Staff (62). After the password, the person
gets a challenge instead of login tokens; it is short-lived, single use, tied to the host it
was made on, and allows a few wrong numbers. The caller gives out tokens only once a number or
a recovery code passes here.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from auth_tokens.models import TokenKind
from auth_tokens.services import sign_out_everywhere
from auth_tokens.types import Person
from platform_admin.brand import get_product_name
from platform_admin.models import PlatformAdmin
from staff.actors import actor_name, is_platform_visitor, stored_staff
from staff.models import Staff
from tenancy.context import get_active_schema_name
from two_factor.exceptions import (
    ChallengeEndedError,
    TooManyWrongNumbersError,
    TwoFactorUnavailableError,
    WrongNumberError,
)
from two_factor.models import (
    ChallengeStep,
    EventKind,
    RecoveryCode,
    TwoFactorAccount,
    TwoFactorChallenge,
    TwoFactorEvent,
)
from two_factor.numbers import (
    check_number,
    new_recovery_codes,
    new_secret,
    recovery_code_hash,
    setup_uri,
)
from two_factor.sealing import open_secret, seal_secret, secrets_key_is_set

CHALLENGE_LIFETIME = timedelta(minutes=5)
# Wrong numbers allowed on one challenge; the next try finds it ended.
CHALLENGE_TRIES_ALLOWED = 5
# Wrong numbers allowed for one person across all challenges in one window, before a wait.
PERSON_WRONG_NUMBERS_ALLOWED = 10
PERSON_WINDOW = timedelta(minutes=15)
_SALT = "two_factor.challenge"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Challenge:
    """What the person must do next, and the signed token that proves the password passed."""

    token: str
    step: ChallengeStep


@dataclass(frozen=True)
class TwoFactorState:
    """Whether someone's app is set up, and how many recovery codes they have left."""

    set_up: bool
    recovery_codes_left: int


class _Outcome(Enum):
    RIGHT = "right"
    WRONG = "wrong"
    TOO_MANY = "too many"


def kind_of(person: Person) -> TokenKind:
    """Whose two-step login this is: a Staff member's or a Platform Admin's."""
    return TokenKind.STAFF if isinstance(person, Staff) else TokenKind.PLATFORM_ADMIN


def _account_of(person: Person) -> TwoFactorAccount | None:
    return TwoFactorAccount.objects.filter(kind=kind_of(person), person_id=person.pk).first()


def is_set_up(person: Person) -> bool:
    """Whether the person has a confirmed authenticator app."""
    account = _account_of(person)
    return account is not None and bool(account.secret)


def state_of(person: Person) -> TwoFactorState:
    """The person's two-step login as shown on their Security page; never the secret."""
    account = _account_of(person)
    if account is None or not account.secret:
        return TwoFactorState(set_up=False, recovery_codes_left=0)
    left = account.recovery_codes.filter(used_at__isnull=True).count()
    return TwoFactorState(set_up=True, recovery_codes_left=left)


def check_available() -> None:
    """Refuse to start using two-step login on a server that can't seal secrets.

    Raises:
        TwoFactorUnavailableError: TWO_FACTOR_SECRETS_KEY isn't set to a Fernet key.
    """
    if not secrets_key_is_set():
        raise TwoFactorUnavailableError


def record(person: Person, what: EventKind, by: Staff | None = None) -> None:
    """Write one append-only record of a change to the person's two-step login.

    `by` is whoever did it, when it isn't the person themselves: an Admin, or a Platform Admin
    in Support Access (named as themselves, marked, and never linked as Staff).
    """
    TwoFactorEvent.objects.create(
        kind=kind_of(person), person_id=person.pk, what=what, **_who(person, by)
    )


def _who(person: Person, by: Staff | None) -> dict[str, object]:
    if by is not None:
        done_by = stored_staff(by)
        return {
            "by_staff_id": None if done_by is None else done_by.pk,
            "by_name": actor_name(by),
            "by_platform_admin": is_platform_visitor(by),
        }
    if isinstance(person, Staff):
        return {"by_staff_id": person.pk, "by_name": str(person.name)}
    return {"by_name": str(person.name), "by_platform_admin": True}


def set_up_ids(kind: TokenKind, person_ids: list[int]) -> set[int]:
    """Which of these people have a confirmed app, in one query (for lists such as Employees)."""
    return set(
        TwoFactorAccount.objects.filter(kind=kind, person_id__in=person_ids)
        .exclude(secret="")
        .values_list("person_id", flat=True)
    )


def start_challenge(person: Person, host: str) -> Challenge:
    """Start the second step for a person whose password just passed, on `host`.

    Any earlier challenge of theirs ends, so only the newest one works.
    """
    now = timezone.now()
    kind = kind_of(person)
    TwoFactorChallenge.objects.filter(kind=kind, person_id=person.pk, ended_at__isnull=True).update(
        ended_at=now
    )
    key = secrets.token_urlsafe(32)
    step = ChallengeStep.VERIFY if is_set_up(person) else ChallengeStep.SETUP
    TwoFactorChallenge.objects.create(
        key_hash=_hash(key),
        kind=kind,
        person_id=person.pk,
        host=host,
        step=step,
        created_at=now,
        expires_at=now + CHALLENGE_LIFETIME,
    )
    payload = {"c": key, "h": host, "s": get_active_schema_name()}
    token = signing.dumps(payload, key=settings.JWT_SIGNING_KEY, salt=_SALT)
    return Challenge(token=token, step=step)


@transaction.atomic
def begin_setup(token: str, host: str) -> tuple[Person, str]:
    """Make a new secret for a setup challenge and return the person and its `otpauth://` URI.

    The URI is given once per challenge; the secret only counts once a number confirms it.

    Raises:
        ChallengeEndedError: The challenge can't be used, or the URI was already given.
    """
    challenge = _open(token, host, ChallengeStep.SETUP)
    if challenge.setup_given:
        challenge.ended_at = timezone.now()
        challenge.save(update_fields=["ended_at"])
        raise ChallengeEndedError
    person = _person_of(challenge)
    account, _ = TwoFactorAccount.objects.get_or_create(kind=challenge.kind, person_id=person.pk)
    secret = new_secret()
    account.pending_secret = seal_secret(secret)
    account.save(update_fields=["pending_secret"])
    challenge.setup_given = True
    challenge.save(update_fields=["setup_given"])
    # The app shows the product's name now; an app set up earlier keeps the old one (ticket 15).
    return person, setup_uri(secret, person.email, get_product_name())


def confirm_setup(token: str, number: str, host: str) -> tuple[Person, list[str]]:
    """Confirm the new app with one number: it is set up, and 10 recovery codes are made.

    Every other login of the person ends (the caller logs this one in).

    Returns:
        The person and their new recovery codes, shown once.

    Raises:
        ChallengeEndedError: The challenge can't be used, or setup hasn't started.
        WrongNumberError: The number is wrong.
        TooManyWrongNumbersError: Too many wrong numbers lately.
    """
    codes: list[str] = []

    def judge(account: TwoFactorAccount, challenge: TwoFactorChallenge) -> _Outcome:
        if not challenge.setup_given or not account.pending_secret:
            raise ChallengeEndedError
        checked = check_number(
            open_secret(account.pending_secret), number, at=timezone.now(), last_step=None
        )
        if checked.accepted_step is None:
            _log_wrong(account, replayed=False)
            return _Outcome.WRONG
        account.secret = account.pending_secret
        account.pending_secret = ""
        account.last_step = checked.accepted_step
        account.set_up_at = timezone.now()
        account.save(update_fields=["secret", "pending_secret", "last_step", "set_up_at"])
        codes.extend(_replace_codes(account))
        return _Outcome.RIGHT

    person = _attempt(token, host, ChallengeStep.SETUP, judge)
    record(person, EventKind.SET_UP)
    sign_out_everywhere(person)
    logger.info("two-step login set up: kind=%s id=%s", kind_of(person).value, person.pk)
    return person, codes


def verify_with_number(token: str, number: str, host: str) -> Person:
    """Pass the second step with a number from the app, and return who it is.

    Raises:
        ChallengeEndedError: The challenge can't be used.
        WrongNumberError: The number is wrong, too old or too new, or already used.
        TooManyWrongNumbersError: Too many wrong numbers lately.
    """
    person = _attempt(
        token, host, ChallengeStep.VERIFY, lambda account, _: _judge_number(account, number)
    )
    _log_passed(person)
    return person


def verify_with_recovery_code(token: str, code: str, host: str) -> Person:
    """Pass the second step with a recovery code, used up now; every other login ends.

    Raises:
        ChallengeEndedError: The challenge can't be used.
        WrongNumberError: The code isn't one of the person's unused codes.
        TooManyWrongNumbersError: Too many wrong tries lately.
    """
    person = _attempt(
        token, host, ChallengeStep.VERIFY, lambda account, _: _judge_recovery_code(account, code)
    )
    record(person, EventKind.RECOVERY_CODE_USED)
    sign_out_everywhere(person)
    _log_passed(person)
    return person


def check_number_of(person: Person, number: str) -> None:
    """Accept only a current, unused number from the person's own app (Account changes).

    Raises:
        WrongNumberError: The number is wrong, or the app isn't set up.
        TooManyWrongNumbersError: Too many wrong numbers lately.
    """
    _check_own(person, lambda account: _judge_number(account, number))


def use_recovery_code_of(person: Person, code: str) -> None:
    """Accept one of the person's unused recovery codes in place of a number, using it up.

    Raises:
        WrongNumberError: The code isn't one of the person's unused codes.
        TooManyWrongNumbersError: Too many wrong tries lately.
    """
    _check_own(person, lambda account: _judge_recovery_code(account, code))
    record(person, EventKind.RECOVERY_CODE_USED)


@transaction.atomic
def replace_recovery_codes(person: Person) -> list[str]:
    """Make 10 new recovery codes for the person; the old ones stop working. Shown once."""
    account = TwoFactorAccount.objects.select_for_update().get(
        kind=kind_of(person), person_id=person.pk
    )
    codes = _replace_codes(account)
    record(person, EventKind.NEW_RECOVERY_CODES)
    logger.info("new recovery codes made: kind=%s id=%s", kind_of(person).value, person.pk)
    return codes


def forget(person: Person) -> None:
    """Remove the person's app and recovery codes, and end their open challenges."""
    kind = kind_of(person)
    TwoFactorAccount.objects.filter(kind=kind, person_id=person.pk).delete()
    TwoFactorChallenge.objects.filter(kind=kind, person_id=person.pk, ended_at__isnull=True).update(
        ended_at=timezone.now()
    )


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _open(token: str, host: str, step: ChallengeStep) -> TwoFactorChallenge:
    """The open challenge `token` names, locked for this request.

    Raises:
        ChallengeEndedError: Forged, expired, used, ended, for another step or another host.
    """
    try:
        payload = signing.loads(
            token,
            key=settings.JWT_SIGNING_KEY,
            salt=_SALT,
            max_age=CHALLENGE_LIFETIME,
        )
    except signing.BadSignature as error:
        raise ChallengeEndedError from error
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(name), str) for name in ("c", "h", "s")
    ):
        raise ChallengeEndedError
    if payload["h"] != host or payload["s"] != get_active_schema_name():
        logger.warning("two-step challenge used on the wrong host")
        raise ChallengeEndedError
    challenge = (
        TwoFactorChallenge.objects.select_for_update().filter(key_hash=_hash(payload["c"])).first()
    )
    if (
        challenge is None
        or challenge.ended_at is not None
        or challenge.expires_at <= timezone.now()
        or challenge.host != host
        or challenge.step != step
    ):
        raise ChallengeEndedError
    return challenge


def _person_of(challenge: TwoFactorChallenge) -> Person:
    model: type[Staff] | type[PlatformAdmin] = (
        Staff if challenge.kind == TokenKind.STAFF else PlatformAdmin
    )
    person = model.objects.filter(pk=challenge.person_id, is_active=True).first()
    if person is None:
        raise ChallengeEndedError
    return person


type _Judge = Callable[[TwoFactorAccount, TwoFactorChallenge], _Outcome]


def _attempt(token: str, host: str, step: ChallengeStep, judge: _Judge) -> Person:
    """Judge one try on a challenge; a wrong try is counted (and kept) before it is refused."""
    with transaction.atomic():
        challenge = _open(token, host, step)
        person = _person_of(challenge)
        account = (
            TwoFactorAccount.objects.select_for_update()
            .filter(kind=challenge.kind, person_id=person.pk)
            .first()
        )
        if account is None:
            raise ChallengeEndedError
        outcome = _Outcome.TOO_MANY if _too_many(account) else judge(account, challenge)
        if outcome == _Outcome.WRONG:
            _count_wrong(account)
            challenge.wrong_numbers += 1
            if challenge.wrong_numbers >= CHALLENGE_TRIES_ALLOWED:
                challenge.ended_at = timezone.now()
        elif outcome == _Outcome.RIGHT:
            _reset_wrong(account)
            challenge.ended_at = timezone.now()
        challenge.save(update_fields=["wrong_numbers", "ended_at"])
    _raise_for(outcome, person)
    return person


def _check_own(person: Person, judge: Callable[[TwoFactorAccount], _Outcome]) -> None:
    with transaction.atomic():
        account = (
            TwoFactorAccount.objects.select_for_update()
            .filter(kind=kind_of(person), person_id=person.pk)
            .exclude(secret="")
            .first()
        )
        if account is None:
            raise WrongNumberError
        outcome = _Outcome.TOO_MANY if _too_many(account) else judge(account)
        if outcome == _Outcome.WRONG:
            _count_wrong(account)
        elif outcome == _Outcome.RIGHT:
            _reset_wrong(account)
    _raise_for(outcome, person)


def _raise_for(outcome: _Outcome, person: Person) -> None:
    if outcome == _Outcome.TOO_MANY:
        logger.warning(
            "two-step wrong numbers limit reached: kind=%s id=%s", kind_of(person).value, person.pk
        )
        raise TooManyWrongNumbersError
    if outcome == _Outcome.WRONG:
        raise WrongNumberError


def _judge_number(account: TwoFactorAccount, number: str) -> _Outcome:
    if not account.secret:
        return _Outcome.WRONG
    checked = check_number(
        open_secret(account.secret), number, at=timezone.now(), last_step=account.last_step
    )
    if checked.accepted_step is None:
        _log_wrong(account, replayed=checked.replayed)
        return _Outcome.WRONG
    account.last_step = checked.accepted_step
    account.save(update_fields=["last_step"])
    return _Outcome.RIGHT


def _judge_recovery_code(account: TwoFactorAccount, code: str) -> _Outcome:
    found = (
        account.recovery_codes.select_for_update()
        .filter(code_hash=recovery_code_hash(code), used_at__isnull=True)
        .first()
    )
    if found is None:
        logger.warning(
            "two-step recovery code refused: kind=%s id=%s", account.kind, account.person_id
        )
        return _Outcome.WRONG
    found.used_at = timezone.now()
    found.save(update_fields=["used_at"])
    logger.warning("recovery code used: kind=%s id=%s", account.kind, account.person_id)
    return _Outcome.RIGHT


def _replace_codes(account: TwoFactorAccount) -> list[str]:
    account.recovery_codes.all().delete()
    codes = new_recovery_codes()
    RecoveryCode.objects.bulk_create(
        RecoveryCode(account=account, code_hash=recovery_code_hash(code)) for code in codes
    )
    return codes


def _too_many(account: TwoFactorAccount) -> bool:
    return _in_window(account) and account.wrong_numbers >= PERSON_WRONG_NUMBERS_ALLOWED


def _in_window(account: TwoFactorAccount) -> bool:
    started = account.wrong_window_started_at
    return started is not None and started > timezone.now() - PERSON_WINDOW


def _count_wrong(account: TwoFactorAccount) -> None:
    if not _in_window(account):
        account.wrong_window_started_at = timezone.now()
        account.wrong_numbers = 0
    account.wrong_numbers += 1
    account.save(update_fields=["wrong_numbers", "wrong_window_started_at"])


def _reset_wrong(account: TwoFactorAccount) -> None:
    account.wrong_numbers = 0
    account.wrong_window_started_at = None
    account.save(update_fields=["wrong_numbers", "wrong_window_started_at"])


def _log_wrong(account: TwoFactorAccount, *, replayed: bool) -> None:
    message = "two-step number replayed" if replayed else "two-step number refused"
    logger.warning("%s: kind=%s id=%s", message, account.kind, account.person_id)


def _log_passed(person: Person) -> None:
    logger.info("two-step login passed: kind=%s id=%s", kind_of(person).value, person.pk)
