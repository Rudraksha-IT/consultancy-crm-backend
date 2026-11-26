"""Public services for Platform Admins and the Consultancy lifecycle they manage."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from auth_tokens.services import issue_tokens, sign_out_everywhere
from auth_tokens.types import Device, TokenPair
from modules.services import get_enabled_modules
from platform_admin.exceptions import (
    PlatformAdminDetailsError,
    TooManyPasswordTriesError,
    WrongCurrentPlatformPasswordError,
)
from platform_admin.models import (
    PLATFORM_ADMIN_NAME_MAX_LENGTH,
    PlatformAdmin,
    PlatformAdminChange,
)
from platform_admin.types import ConsultancyOverview, PlatformAdminDetails
from staff.exceptions import PasswordRulesNotMetError
from staff.passwords import unmet_password_rules
from staff.services import normalize_email
from tenancy.models import Consultancy, Domain, SetupState
from tenancy.services import get_status
from tenancy.subdomains import subdomain_from_host
from two_factor.exceptions import WrongNumberError
from two_factor.services import check_number_of, is_set_up

logger = logging.getLogger(__name__)


EMAIL_MAX_LENGTH = 254
PASSWORD_MAX_LENGTH = 128
# Wrong current passwords allowed on the Account page in one window, before a wait (issue 59).
WRONG_PASSWORD_TRIES_ALLOWED = 5
WRONG_PASSWORD_WINDOW = timedelta(minutes=15)

NAME_MESSAGE = "Enter a name of 1 to 200 letters, using only spaces, ' . and - between them."
EMAIL_MESSAGE = "Enter a valid email address."
EMAIL_TAKEN_MESSAGE = "Another Platform Admin already uses this email."
SAME_PASSWORD_MESSAGE = "Choose a password different from your current one."  # noqa: S105
PASSWORD_REQUIRED_MESSAGE = "A password is needed."  # noqa: S105  # a message, not a secret
_RULES_TEXT = "with a capital letter, a small letter, a number and a symbol."
PASSWORD_RULES_MESSAGE = f"Use at least 8 and at most 128 characters, {_RULES_TEXT}"

# A letter first, then letters (any alphabet), spaces, apostrophes, full stops and hyphens. A
# name never needs markup, quotes, brackets or semicolons, so HTML- and SQL-shaped text is out.
_NAME = re.compile(r"[^\W\d_](?:[^\W\d_]|[ '.\-])*\Z")


def platform_admin_problems(
    details: PlatformAdminDetails, password: str | None, *, creating: bool
) -> dict[str, str]:
    """Return what is wrong with these details, by field ("name", "email", "password").

    The name is checked after trimming and the email after trimming and lower-casing. A
    password is needed when `creating`; otherwise None leaves it unchanged. Messages never
    repeat a value.
    """
    found = {
        "name": name_problem(details.name.strip()),
        "email": _email_problem(normalize_email(details.email)),
        "password": _password_problem(password, creating=creating),
    }
    return {field: message for field, message in found.items() if message is not None}


def name_problem(name: str) -> str | None:
    """Return why a person's name (already trimmed) is refused, or None.

    1 to 200 letters with spaces, ' . and - between them: no markup, quotes or semicolons.
    """
    if len(name) > PLATFORM_ADMIN_NAME_MAX_LENGTH or not _NAME.match(name):
        return NAME_MESSAGE
    return None


def _email_problem(email: str) -> str | None:
    if len(email) > EMAIL_MAX_LENGTH or not _is_email(email):
        return EMAIL_MESSAGE
    return None


def _password_problem(password: str | None, *, creating: bool) -> str | None:
    if password is None:
        return PASSWORD_REQUIRED_MESSAGE if creating else None
    if len(password) > PASSWORD_MAX_LENGTH or unmet_password_rules(password):
        return PASSWORD_RULES_MESSAGE
    return None


def _is_email(email: str) -> bool:
    try:
        validate_email(email)
    except ValidationError:
        return False
    return True


def find_platform_admin(email: str) -> PlatformAdmin | None:
    """Return the Platform Admin with this email, ignoring case and spaces, or None."""
    return PlatformAdmin.objects.filter(email=normalize_email(email)).first()


def save_platform_admin(
    details: PlatformAdminDetails,
    *,
    password: str | None = None,
    admin: PlatformAdmin | None = None,
    by: PlatformAdmin | None = None,
    two_factor_required: bool = False,
) -> PlatformAdmin:
    """Create a Platform Admin, or change one. The only code that does either (issue 59).

    Call it on the public schema. Every creation or change is recorded, never with a password.

    Args:
        details: The name and email, as given; both are trimmed and the email lower-cased.
        password: The raw password: needed to create; when changing, None keeps the old one.
        admin: The Platform Admin to change; None creates a new one.
        by: Who makes the change; None is the system (the deploy).
        two_factor_required: Whether a new Platform Admin starts with two-step login on
            (issue 61). Ignored when changing one: only the Security page changes that.

    Returns:
        The saved Platform Admin. A changed email or password signs it out everywhere (its
        older tokens stop working); the caller gives the current session new tokens.

    Raises:
        PlatformAdminDetailsError: A value breaks a rule, or the email is another Platform
            Admin's. Nothing is saved.
    """
    cleaned = PlatformAdminDetails(name=details.name.strip(), email=normalize_email(details.email))
    problems = platform_admin_problems(cleaned, password, creating=admin is None)
    if "email" not in problems and _email_taken(cleaned.email, admin):
        problems["email"] = EMAIL_TAKEN_MESSAGE
    if problems:
        raise PlatformAdminDetailsError(problems)
    try:
        with transaction.atomic():
            if admin is None:
                return _create(cleaned, password, two_factor_required=two_factor_required)
            return _change(admin, cleaned, password, by)
    except IntegrityError as error:
        # Another save took the email between the check above and this one.
        raise PlatformAdminDetailsError({"email": EMAIL_TAKEN_MESSAGE}) from error


def _email_taken(email: str, admin: PlatformAdmin | None) -> bool:
    others = PlatformAdmin.objects.filter(email=email)
    if admin is not None:
        others = others.exclude(pk=admin.pk)
    return others.exists()


def _create(
    details: PlatformAdminDetails, password: str | None, *, two_factor_required: bool
) -> PlatformAdmin:
    admin = PlatformAdmin(
        name=details.name,
        email=details.email,
        two_factor_required=two_factor_required,
        joined_at=timezone.now(),
    )
    admin.set_password(password)
    admin.save()
    PlatformAdminChange.objects.create(
        platform_admin=admin, created=True, fields=["name", "email", "password"]
    )
    logger.info("platform admin added: id=%s", admin.pk)
    return admin


def _change(
    admin: PlatformAdmin,
    details: PlatformAdminDetails,
    password: str | None,
    by: PlatformAdmin | None,
) -> PlatformAdmin:
    changed = [
        field for field in ("name", "email") if getattr(admin, field) != getattr(details, field)
    ]
    admin.name = details.name
    admin.email = details.email
    if password is not None:
        admin.set_password(password)
        changed.append("password")
    if not changed:
        return admin
    admin.save(update_fields=changed)
    if "email" in changed or "password" in changed:
        sign_out_everywhere(admin)
    PlatformAdminChange.objects.create(platform_admin=admin, by=by, fields=changed)
    logger.info("platform admin changed: id=%s fields=%s", admin.pk, ",".join(changed))
    return admin


def check_current_password(admin: PlatformAdmin, current: str) -> None:
    """Accept only the Platform Admin's own current password, with a limit on wrong tries.

    After `WRONG_PASSWORD_TRIES_ALLOWED` wrong tries in `WRONG_PASSWORD_WINDOW`, every try is
    refused until the window ends. A right password starts the count again.

    Raises:
        TooManyPasswordTriesError: Too many wrong tries; nothing was checked.
        WrongCurrentPlatformPasswordError: `current` isn't the password.
    """
    # The count is saved before refusing: raising inside the transaction would undo it.
    outcome = _count_password_try(admin, current)
    if outcome == _TOO_MANY:
        logger.warning("platform admin password tries limit reached: id=%s", admin.pk)
        raise TooManyPasswordTriesError
    if outcome == _WRONG:
        logger.warning("platform admin current password refused: id=%s", admin.pk)
        raise WrongCurrentPlatformPasswordError


_RIGHT, _WRONG, _TOO_MANY = "right", "wrong", "too many"


@transaction.atomic
def _count_password_try(admin: PlatformAdmin, current: str) -> str:
    """Check `current` and count a wrong try; one request at a time per Platform Admin."""
    locked = PlatformAdmin.objects.select_for_update().get(pk=admin.pk)
    now = timezone.now()
    started = locked.wrong_password_window_started_at
    in_window = started is not None and started > now - WRONG_PASSWORD_WINDOW
    tries = locked.wrong_password_tries if in_window else 0
    if tries >= WRONG_PASSWORD_TRIES_ALLOWED:
        return _TOO_MANY
    outcome = _RIGHT if locked.check_password(current) else _WRONG
    if outcome == _WRONG and not in_window:
        locked.wrong_password_window_started_at = now
    locked.wrong_password_tries = tries + 1 if outcome == _WRONG else 0
    locked.save(update_fields=["wrong_password_tries", "wrong_password_window_started_at"])
    return outcome


def authenticate_platform_admin(email: str, password: str) -> PlatformAdmin | None:
    """Return the active Platform Admin with these credentials, or None."""
    admin = PlatformAdmin.objects.filter(email=normalize_email(email)).first()
    if admin is None:
        # Hash anyway, so an unknown email takes as long to reject as a wrong password.
        PlatformAdmin().set_password(password)
        return None
    if not admin.check_password(password) or not admin.is_active:
        return None
    return admin


@dataclass(frozen=True)
class AccountProof:
    """What an Account change is made with.

    The current password, and once two-step login is set up, a current number from the app
    (issue 61).
    """

    password: str = field(repr=False)
    code: str | None = field(default=None, repr=False)


def _check_proof(admin: PlatformAdmin, proof: AccountProof) -> None:
    check_current_password(admin, proof.password)
    check_second_step(admin, proof.code)


def change_my_platform_name(
    admin: PlatformAdmin, name: str, *, device: str
) -> tuple[PlatformAdmin, TokenPair]:
    """The Account page: a Platform Admin changes their own name, without a password (issue 65).

    The email is never changed here (only from the deploy's values, issue 59), so nobody is
    signed out.

    Returns:
        The saved Platform Admin and tokens for this device.

    Raises:
        PlatformAdminDetailsError: The name is refused.
    """
    details = PlatformAdminDetails(name=name, email=admin.email)
    saved = save_platform_admin(details, admin=admin, by=admin)
    return saved, _tokens_for_this_device(saved, device)


def change_my_platform_password(
    admin: PlatformAdmin, proof: AccountProof, new: str, device: str
) -> TokenPair:
    """The Account page: a Platform Admin sets a new password (issue 59).

    Returns:
        New tokens for this device; every other session is signed out.

    Raises:
        PasswordRulesNotMetError: The new password breaks a rule (checked first).
        TooManyPasswordTriesError, WrongCurrentPlatformPasswordError: see
            `check_current_password`.
        PlatformAdminDetailsError: The new password is the current one, or too long.
        WrongNumberError, TooManyWrongNumbersError: see `check_second_step`.
    """
    unmet = unmet_password_rules(new)
    if unmet:
        raise PasswordRulesNotMetError(unmet)
    _check_proof(admin, proof)
    if admin.check_password(new):
        raise PlatformAdminDetailsError({"password": SAME_PASSWORD_MESSAGE})
    details = PlatformAdminDetails(name=admin.name, email=admin.email)
    saved = save_platform_admin(details, password=new, admin=admin, by=admin)
    return _tokens_for_this_device(saved, device)


def check_second_step(admin: PlatformAdmin, code: str | None) -> None:
    """Once two-step login is set up, an Account change needs a current number too (issue 61).

    Raises:
        WrongNumberError: No number was given, or it is wrong or already used.
        TooManyWrongNumbersError: Too many wrong numbers lately.
    """
    if not is_set_up(admin):
        return
    if code is None:
        raise WrongNumberError
    check_number_of(admin, code)


def _tokens_for_this_device(admin: PlatformAdmin, device: str) -> TokenPair:
    return issue_tokens(admin, Device(id=device, signed_in_at=timezone.now()))


def _build_overview(consultancy: Consultancy, host: str) -> ConsultancyOverview:
    """Return the Platform's summary of a Consultancy reached at the given host."""
    return ConsultancyOverview(
        id=consultancy.public_id,
        name=consultancy.name,
        subdomain=subdomain_from_host(host),
        host=host,
        status=get_status(consultancy),
        status_reason=consultancy.status_reason,
        evaluation_ends_at=consultancy.evaluation_ends_at,
        paid_until=consultancy.paid_until,
        modules=get_enabled_modules(consultancy),
        setup_state=SetupState(consultancy.setup_state),
    )


def get_overview(consultancy: Consultancy) -> ConsultancyOverview:
    """Return the Platform's summary of one Consultancy."""
    domain = Domain.objects.get(tenant=consultancy, is_primary=True)
    return _build_overview(consultancy, domain.domain)


def list_consultancies() -> list[ConsultancyOverview]:
    """Return every Consultancy, whatever its Status, ordered by name."""
    domains = Domain.objects.filter(is_primary=True).select_related("tenant")
    return [
        _build_overview(domain.tenant, domain.domain)
        for domain in domains.order_by("tenant__name", "tenant_id")
    ]
