"""Public services for one-time Codes (glossary: Code).

A Code is 6 digits, works for 30 minutes, once, for one person and one purpose, and stops
working after a few wrong tries. Sending a new one replaces the old one.

Two scopes, never mixed (issue 63): a Staff member's Codes live in their Consultancy's schema
(call inside it), a Platform Admin's on the Platform side (call on the public schema only).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from codes.exceptions import CodeLimitReachedError, InvalidCodeError, WrongCodeScopeError
from codes.models import SALT_LENGTH, CodeRecord, OneTimeCode
from codes.types import CodePurpose
from platform_admin.models import PlatformAdmin, PlatformAdminCode
from staff.models import Staff
from tenancy.context import is_public_schema_active

# Who a Code can belong to: a Staff member (office scope) or a Platform Admin (Platform scope).
CodeHolder = Staff | PlatformAdmin

CODE_DIGITS = 6
CODE_LIFETIME = timedelta(minutes=30)
# Wrong tries before a Code stops working.
MAX_ATTEMPTS = 5
# Codes sent per Staff member and purpose in one hour; with MAX_ATTEMPTS this caps guessing.
MAX_CODES_PER_HOUR = 5
_LIMIT_WINDOW = timedelta(hours=1)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Scope:
    """Where one person's Codes are kept, and how the logs name the person (ids only)."""

    codes: QuerySet[Any]
    owner: dict[str, CodeHolder]
    person: QuerySet[Any]
    who: str


def _scope_of(holder: CodeHolder) -> _Scope | None:
    """The person's Codes, or None for a Platform Admin outside the Platform's own schema."""
    if isinstance(holder, PlatformAdmin):
        if not is_public_schema_active():
            return None
        return _Scope(
            codes=PlatformAdminCode.objects.filter(platform_admin=holder),
            owner={"platform_admin": holder},
            person=PlatformAdmin.objects.filter(pk=holder.pk),
            who=f"platform_admin_id={holder.pk}",
        )
    return _Scope(
        codes=OneTimeCode.objects.filter(staff=holder),
        owner={"staff": holder},
        person=Staff.objects.filter(pk=holder.pk),
        who=f"staff_id={holder.pk}",
    )


def issue_code(holder: CodeHolder, purpose: CodePurpose) -> str:
    """Make a new Code for `holder`, replacing any earlier one for `purpose`, and return it.

    The caller emails it; it is never stored or logged.

    Raises:
        CodeLimitReachedError: `MAX_CODES_PER_HOUR` were already sent in the last hour.
        WrongCodeScopeError: A Platform Admin's Code was asked for inside a Consultancy.
    """
    scope = _scope_of(holder)
    if scope is None:
        raise WrongCodeScopeError
    now = timezone.now()
    with transaction.atomic():
        # Lock the person so two requests at once can't both slip under the limit.
        scope.person.select_for_update().first()
        mine = scope.codes.filter(purpose=purpose)
        if mine.filter(created_at__gt=now - _LIMIT_WINDOW).count() >= MAX_CODES_PER_HOUR:
            logger.warning("code limit reached: %s purpose=%s", scope.who, purpose.value)
            raise CodeLimitReachedError
        mine.filter(used_at__isnull=True, replaced_at__isnull=True).update(replaced_at=now)
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        salt = secrets.token_hex(SALT_LENGTH // 2)
        scope.codes.model.objects.create(
            **scope.owner,
            purpose=purpose,
            salt=salt,
            code_hash=_hash(salt, code),
            created_at=now,
            expires_at=now + CODE_LIFETIME,
        )
    return code


def use_code(holder: CodeHolder, purpose: CodePurpose, code: str, *, consume: bool = True) -> None:
    """Accept `code` for `holder` and `purpose`, using it up unless `consume` is False.

    A wrong Code counts as a try. Call it outside any transaction a refusal would roll back,
    or that try is forgotten. A Platform Admin's Code works only on the Platform's own schema.

    Raises:
        InvalidCodeError: The Code is wrong, expired, used, replaced, out of tries, or used
            outside its scope.
    """
    scope = _scope_of(holder)
    if scope is None or not _accept(scope, purpose, code, consume=consume):
        raise InvalidCodeError
    logger.info("code accepted: %s purpose=%s", scope.who, purpose.value)


def _accept(scope: _Scope, purpose: CodePurpose, code: str, *, consume: bool) -> bool:
    with transaction.atomic():
        record: CodeRecord | None = (
            scope.codes.select_for_update()
            .filter(
                purpose=purpose,
                used_at__isnull=True,
                replaced_at__isnull=True,
                expires_at__gt=timezone.now(),
                attempts__lt=MAX_ATTEMPTS,
            )
            .first()
        )
        if record is None:
            return False
        this = scope.codes.filter(pk=record.pk)
        if not hmac.compare_digest(record.code_hash, _hash(record.salt, code)):
            this.update(attempts=F("attempts") + 1)
            if record.attempts + 1 >= MAX_ATTEMPTS:
                logger.warning("code out of tries: %s purpose=%s", scope.who, purpose.value)
            return False
        if consume:
            this.update(used_at=timezone.now())
        return True


def _hash(salt: str, code: str) -> str:
    """Keyed with the server's secret, so the database alone never reveals a Code."""
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"{salt}:{code}".encode(), hashlib.sha256).hexdigest()
