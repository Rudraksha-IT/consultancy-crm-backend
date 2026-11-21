"""Public services for login tokens (ADR 0003). Call them on the schema the person belongs to.

Every token names its schema (`tenant_schema`) and who it is for (`kind`, `user_id`). It is only
accepted on that schema, while the person is active and their `auth_version` is unchanged.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, QuerySet
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.tokens import Token
from rest_framework_simplejwt.utils import datetime_from_epoch

from auth_tokens.exceptions import InvalidTokenError, TokenRefusal
from auth_tokens.models import UNKNOWN_DEVICE_NAME, IssuedRefreshToken, TokenKind
from auth_tokens.tokens import AccessToken, RefreshToken
from auth_tokens.types import Browser, Device, Person, SignedInDevice, TokenClaims, TokenPair
from config.public_ids import pk_for
from config.serializers import parse_record_id
from platform_admin.models import PlatformAdmin, SupportVisit
from platform_admin.support_access import find_visitor
from staff.models import PlatformVisitor, Staff
from tenancy.context import get_active_schema_name, is_public_schema_active

# Of the devices no longer logged in, only this many of the newest are kept and listed (issue 71).
KEPT_LOGGED_OUT_DEVICES = 3
_DEVICE_ID_SALT = "auth_tokens.device"

logger = logging.getLogger(__name__)


def issue_tokens(
    person: Person, device: Device | None = None, browser: Browser | None = None
) -> TokenPair:
    """Log `person` in on the active schema and return their new access and refresh tokens.

    Without `device` this is a new login, from `browser` if the Next.js server named it: a
    browser it knows already gets its old device back (its older login there ends), otherwise
    a new device. With `device`, new tokens for that same device.

    Raises:
        ValueError: The person doesn't belong here (Staff on the public schema, or a Platform
            Admin inside a Consultancy).
    """
    kind = _kind_of(person)
    if kind not in _kinds_served_here():
        message = f"A {kind.label} can't be given tokens on schema {get_active_schema_name()}."
        raise ValueError(message)
    is_new_login = device is None
    device = _named(kind, person, device or _login_device(kind, person, browser))
    claims: dict[str, Any] = {
        "tenant_schema": get_active_schema_name(),
        "kind": kind.value,
        "auth_version": person.auth_version,
        "user_id": str(_public_id(person)),
        "device": device.id,
    }
    if isinstance(person, PlatformVisitor):
        claims["visit"] = str(person.support_visit_public_id)
    refresh = _new_token(RefreshToken(), claims, settings.JWT_REFRESH_TOKEN_LIFETIME)
    access = _new_token(AccessToken(), claims, settings.JWT_ACCESS_TOKEN_LIFETIME)
    IssuedRefreshToken.objects.create(
        jti=refresh["jti"],
        kind=kind,
        person_id=_person_id(person),
        device_id=device.id,
        device_name=device.name,
        signed_in_at=device.signed_in_at,
        issued_at=refresh.current_time,
        expires_at=datetime_from_epoch(refresh["exp"]),
    )
    if is_new_login:
        _tidy_devices(kind, _person_id(person))
    return TokenPair(access=str(access), refresh=str(refresh))


def _login_device(kind: TokenKind, person: Person, browser: Browser | None) -> Device:
    """The device a new login belongs to; the browser's older login on it ends."""
    now = timezone.now()
    name = UNKNOWN_DEVICE_NAME if browser is None else browser.name
    if browser is None or browser.key is None:
        return Device(id=uuid.uuid4().hex, signed_in_at=now, name=name)
    # Never the browser's own value: one only this server can make, different for each person.
    value = f"{kind.value}:{_person_id(person)}:{browser.key}"
    device_id = salted_hmac(_DEVICE_ID_SALT, value, algorithm="sha256").hexdigest()[:32]
    IssuedRefreshToken.objects.filter(
        kind=kind, person_id=_person_id(person), device_id=device_id, revoked_at__isnull=True
    ).update(revoked_at=now)
    return Device(id=device_id, signed_in_at=now, name=name)


def _named(kind: TokenKind, person: Person, device: Device) -> Device:
    """`device` with its name: the one given, or the one it already has."""
    if device.name:
        return device
    known = (
        IssuedRefreshToken.objects.filter(
            kind=kind, person_id=_person_id(person), device_id=device.id
        )
        .order_by("-issued_at")
        .values_list("device_name", flat=True)
        .first()
    )
    return Device(id=device.id, signed_in_at=device.signed_in_at, name=known or UNKNOWN_DEVICE_NAME)


def authenticate_access_token(raw: str) -> tuple[Person, TokenClaims]:
    """Return the active person an access token is for, on the active schema, and its claims.

    Raises:
        InvalidTokenError: The token is invalid, expired, for another schema, or out of date.
    """
    claims = read_access_token(raw)
    person = _find_person(claims)
    return person, _with_numbers(claims, person)


def read_access_token(raw: str) -> TokenClaims:
    """Verify an access token's signature, expiry and place, and return its claims.

    Raises:
        InvalidTokenError: The token is invalid, expired, or not for the active schema.
    """
    return _read(AccessToken, raw)


@transaction.atomic
def rotate_refresh_token(raw: str) -> TokenPair:
    """Swap a refresh token for a new pair; the old refresh token never works again.

    Raises:
        InvalidTokenError: The refresh token is invalid, expired, revoked or out of date.
    """
    claims = _read(RefreshToken, raw)
    person = _find_person(claims)
    record = _outstanding(_with_numbers(claims, person)).select_for_update().first()
    if record is None:
        raise InvalidTokenError(TokenRefusal.REVOKED)
    record.revoked_at = timezone.now()
    record.save(update_fields=["revoked_at"])
    return issue_tokens(person, Device(id=record.device_id, signed_in_at=record.signed_in_at))


def revoke_refresh_token(raw: str) -> TokenClaims:
    """Log out one device: its refresh token never works again. Return whose it was.

    Raises:
        InvalidTokenError: The refresh token is invalid, expired or not for this schema.
    """
    claims = _read(RefreshToken, raw)
    claims = replace(
        claims,
        user_id=_person_number(claims.kind, claims.user),
        visit_id=None if claims.visit is None else _visit_number(claims.visit),
    )
    _outstanding(claims).update(revoked_at=timezone.now())
    _tidy_devices(claims.kind, claims.user_id)
    return claims


@transaction.atomic
def sign_out_everywhere(person: Person) -> None:
    """End every login of `person` at once: access tokens too, before they expire.

    For later tickets: password change, reset by Code, deactivation and Platform sign-outs.
    """
    kind = _kind_of(person)
    type(person).objects.filter(pk=person.pk).update(auth_version=F("auth_version") + 1)
    person.refresh_from_db(fields=["auth_version"])
    IssuedRefreshToken.objects.filter(
        kind=kind, person_id=person.pk, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())
    logger.info("Signed out everywhere: kind=%s, person_id=%s", kind.value, person.pk)


def list_signed_in_devices(person: Person, current_device: str) -> list[SignedInDevice]:
    """Return `person`'s Signed-in devices, `current_device` first (issue 71).

    Then the other devices still logged in, then only the newest logged-out ones. A device is
    logged in while it holds an unrevoked, unexpired refresh token.
    """
    devices = [
        SignedInDevice(
            name=summary.name,
            signed_in_at=summary.signed_in_at,
            last_used_at=summary.last_used_at,
            logged_out_at=summary.logged_out_at,
            is_current=summary.device_id == current_device,
        )
        for summary in _device_summaries(_kind_of(person), _person_id(person))
    ]
    signed_in = sorted(
        (device for device in devices if device.logged_out_at is None),
        key=lambda device: (not device.is_current, -device.last_used_at.timestamp()),
    )
    return signed_in + _newest_logged_out(devices)


@transaction.atomic
def sign_out_other_devices(person: Person, current_device: str) -> TokenPair:
    """End every login of `person` at once, then log this device in again with new tokens.

    Every other device's tokens stop working straight away, access tokens too; they then show
    as logged out.
    """
    record = _still_signed_in(person).filter(device_id=current_device).first()
    signed_in_at = timezone.now() if record is None else record.signed_in_at
    sign_out_everywhere(person)
    tokens = issue_tokens(person, Device(id=current_device, signed_in_at=signed_in_at))
    _tidy_devices(_kind_of(person), _person_id(person))
    return tokens


@dataclass(frozen=True)
class _DeviceSummary:
    """One device of a person, from all its refresh tokens; `logged_out_at` None if logged in."""

    device_id: str
    name: str
    signed_in_at: datetime
    last_used_at: datetime
    logged_out_at: datetime | None
    newest_token_id: int


def _device_summaries(kind: TokenKind, person_id: int) -> list[_DeviceSummary]:
    now = timezone.now()
    records = IssuedRefreshToken.objects.filter(kind=kind, person_id=person_id).order_by(
        "-issued_at", "-id"
    )
    grouped: dict[str, list[IssuedRefreshToken]] = {}
    for record in records:
        grouped.setdefault(record.device_id, []).append(record)
    return [_summary(device_id, rows, now) for device_id, rows in grouped.items()]


def _summary(device_id: str, rows: list[IssuedRefreshToken], now: datetime) -> _DeviceSummary:
    """Summarise one device's tokens, the newest first."""
    newest = rows[0]
    ended = [row.revoked_at or row.expires_at for row in rows]
    is_logged_in = any(row.revoked_at is None and row.expires_at > now for row in rows)
    return _DeviceSummary(
        device_id=device_id,
        name=newest.device_name,
        signed_in_at=newest.signed_in_at,
        last_used_at=newest.issued_at,
        logged_out_at=None if is_logged_in else max(ended),
        newest_token_id=newest.pk,
    )


def _newest_logged_out[T: (SignedInDevice, _DeviceSummary)](devices: list[T]) -> list[T]:
    logged_out = [device for device in devices if device.logged_out_at is not None]
    logged_out.sort(key=lambda device: device.logged_out_at or device.last_used_at, reverse=True)
    return logged_out[:KEPT_LOGGED_OUT_DEVICES]


def _tidy_devices(kind: TokenKind, person_id: int) -> None:
    """Forget logged-out devices past the newest few, and the used-up tokens of every device.

    Only tokens that can never be used again are removed (revoked or expired), and each device
    keeps its newest token, which says when it logged in and out.
    """
    summaries = _device_summaries(kind, person_id)
    kept = {summary.device_id for summary in summaries if summary.logged_out_at is None}
    kept |= {summary.device_id for summary in _newest_logged_out(summaries)}
    newest = [summary.newest_token_id for summary in summaries if summary.device_id in kept]
    used_up = Q(revoked_at__isnull=False) | Q(expires_at__lte=timezone.now())
    IssuedRefreshToken.objects.filter(kind=kind, person_id=person_id).exclude(
        Q(device_id__in=kept) & (~used_up | Q(pk__in=newest))
    ).delete()


def _still_signed_in(person: Person) -> QuerySet[IssuedRefreshToken]:
    return IssuedRefreshToken.objects.filter(
        kind=_kind_of(person),
        person_id=person.pk,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    )


def _kind_of(person: Person) -> TokenKind:
    if isinstance(person, PlatformVisitor):
        return TokenKind.SUPPORT_ACCESS
    return TokenKind.STAFF if isinstance(person, Staff) else TokenKind.PLATFORM_ADMIN


def _person_id(person: Person) -> int:
    """Whose tokens: the Staff member's or Platform Admin's own id (a visitor's Platform Admin)."""
    return person.platform_admin_id if isinstance(person, PlatformVisitor) else int(person.pk)


def _public_id(person: Person) -> uuid.UUID:
    """Whose tokens, as the token names them: the person's UUID, never a number (ADR 0027)."""
    if isinstance(person, PlatformVisitor):
        return person.platform_admin_public_id
    return person.public_id


def _kinds_served_here() -> frozenset[TokenKind]:
    """Platform Admins on the public schema; Staff and Support Access visits on a Consultancy's."""
    if is_public_schema_active():
        return frozenset({TokenKind.PLATFORM_ADMIN})
    return frozenset({TokenKind.STAFF, TokenKind.SUPPORT_ACCESS})


def _new_token[T: Token](token: T, claims: dict[str, Any], lifetime: timedelta) -> T:
    token.set_exp(lifetime=lifetime)
    for name, value in claims.items():
        token[name] = value
    return token


def _read(token_class: type[Token], raw: str) -> TokenClaims:
    try:
        payload = token_class(raw).payload  # type: ignore[arg-type]  # simplejwt takes the string
    except ExpiredTokenError as error:
        raise InvalidTokenError(TokenRefusal.EXPIRED) from error
    except TokenError as error:
        raise InvalidTokenError from error
    claims = _claims_from(payload)
    if claims.tenant_schema != get_active_schema_name():
        raise InvalidTokenError(TokenRefusal.WRONG_CONSULTANCY)
    if claims.kind not in _kinds_served_here():
        raise InvalidTokenError(TokenRefusal.WRONG_KIND)
    return claims


def _claims_from(payload: dict[str, Any]) -> TokenClaims:
    """Return the claims we rely on, refusing a token where any is missing or the wrong type.

    A person or visit named by a number (tokens from before ADR 0027) is refused the same way.
    """
    kind = payload.get("kind")
    if kind not in TokenKind.values:
        raise InvalidTokenError(TokenRefusal.MISSING_CLAIM)
    is_visit = kind == TokenKind.SUPPORT_ACCESS
    return TokenClaims(
        tenant_schema=_text(payload, "tenant_schema"),
        kind=TokenKind(kind),
        user=_record_id(payload, "user_id"),
        auth_version=_whole_number(payload, "auth_version"),
        jti=_text(payload, "jti"),
        device=_text(payload, "device"),
        visit=_record_id(payload, "visit") if is_visit else None,
    )


def _with_numbers(claims: TokenClaims, person: Person) -> TokenClaims:
    """`claims` with the found person's (and visit's) database numbers."""
    visit_id = person.support_visit_id if isinstance(person, PlatformVisitor) else None
    return replace(claims, user_id=_person_id(person), visit_id=visit_id)


def _record_id(payload: dict[str, Any], name: str) -> uuid.UUID:
    """A claim naming a record: its UUID as text; a number or anything else is refused."""
    found = parse_record_id(payload.get(name))
    if found is None:
        raise InvalidTokenError(TokenRefusal.MISSING_CLAIM)
    return found


def _person_number(kind: TokenKind, public_id: uuid.UUID) -> int:
    """The database number of the person a token names; 0 (nobody) when there is none."""
    model: type[Staff] | type[PlatformAdmin] = Staff if kind == TokenKind.STAFF else PlatformAdmin
    return pk_for(model.objects.all(), public_id)


def _visit_number(public_id: uuid.UUID) -> int:
    """The database number of the Support Access visit a token names; 0 when there is none."""
    return pk_for(SupportVisit.objects.all(), public_id)


def _text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise InvalidTokenError(TokenRefusal.MISSING_CLAIM)
    return value


def _whole_number(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidTokenError(TokenRefusal.MISSING_CLAIM)
    return value


def _outstanding(claims: TokenClaims) -> QuerySet[IssuedRefreshToken]:
    return IssuedRefreshToken.objects.filter(
        jti=claims.jti, kind=claims.kind, person_id=claims.user_id, revoked_at__isnull=True
    )


def _find_person(claims: TokenClaims) -> Person:
    person: Person | None
    if claims.kind == TokenKind.SUPPORT_ACCESS:
        person = (
            None
            if claims.visit is None
            else find_visitor(claims.visit, claims.user, claims.auth_version, claims.tenant_schema)
        )
    else:
        model = Staff if claims.kind == TokenKind.STAFF else PlatformAdmin
        person = model.objects.filter(
            public_id=claims.user, is_active=True, auth_version=claims.auth_version
        ).first()
    if person is None:
        raise InvalidTokenError(TokenRefusal.OUT_OF_DATE)
    return person
