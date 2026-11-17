"""Emailing a Staff member the login links of their offices (spec 13e, issue 44).

Call in the public schema, after the answer has gone (see views): the answer and its timing
must be the same whether or not the email belongs to anyone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from central_login.directory import email_hash, normalise_email, offices_for
from central_login.models import CentralLoginRequest
from config.outside_calls import EMAIL_PROVIDER, outside_call
from tenancy.models import Consultancy, Domain
from tenancy.services import is_ready
from tenancy.status import ConsultancyStatus

MAX_EMAILS_PER_HOUR = 3
LIMIT_WINDOW = timedelta(hours=1)
LOGIN_PATH = "/login"
SUBJECT = "Your offices' login pages"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkBase:
    """How the office links are written: the scheme and port the Central login was opened on."""

    scheme: str
    port: str | None


def send_office_links(email: str, base: LinkBase) -> None:
    """Email `email` a login link for each non-Terminated office it belongs to.

    Nothing is sent when it belongs to none, or after `MAX_EMAILS_PER_HOUR` requests for the
    same email in the last hour (whether or not it belongs to anyone). Never raises: a failure
    is logged without the address.
    """
    try:
        if not _within_limit(email):
            logger.info("Central login request handled")
            return
        offices = [office for office in offices_for(email) if _is_open(office)]
        if offices:
            _send(normalise_email(email), offices, base)
        logger.info("Central login request handled")
    except Exception:  # the answer has gone; log it, never re-raise
        # The redaction filter blanks the address an SMTP error may name.
        logger.warning("central login email not sent", exc_info=True)


def _within_limit(email: str) -> bool:
    """Count this request against the email's hourly limit; False once it's used up."""
    hashed = email_hash(email)
    now = timezone.now()
    with transaction.atomic():
        CentralLoginRequest.objects.filter(requested_at__lt=now - LIMIT_WINDOW).delete()
        recent = CentralLoginRequest.objects.select_for_update().filter(email_hash=hashed)
        if len(recent) >= MAX_EMAILS_PER_HOUR:
            return False
        CentralLoginRequest.objects.create(email_hash=hashed, requested_at=now)
    return True


def _is_open(office: Consultancy) -> bool:
    """Not Terminated, and set up (an office still being set up can't be reached, issue 60)."""
    return str(office.status) != ConsultancyStatus.TERMINATED.value and is_ready(office)


def _login_link(office: Consultancy, email: str, base: LinkBase) -> str:
    host = Domain.objects.get(tenant=office, is_primary=True).domain
    address = host if base.port is None else f"{host}:{base.port}"
    return f"{base.scheme}://{address}{LOGIN_PATH}?{urlencode({'email': email})}"


def _send(email: str, offices: list[Consultancy], base: LinkBase) -> None:
    """Send one plain-text email (no HTML, so no office name can become markup)."""
    lines = [
        "Hello,",
        "You asked for the login pages of the offices this email belongs to:",
        *(f"{office.name}\n{_login_link(office, email, base)}" for office in offices),
        "Open the one you want; your email will already be filled in.",
        "If you didn't ask for this, you can ignore this email.",
    ]
    with outside_call(EMAIL_PROVIDER, "send_office_links"):
        send_mail(
            subject=SUBJECT, message="\n\n".join(lines), from_email=None, recipient_list=[email]
        )
