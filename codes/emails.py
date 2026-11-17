"""Emails that carry a Code. Sent through Django's mail interface, which tests replace."""

from __future__ import annotations

from django.core.mail import send_mail

from codes.services import CODE_LIFETIME
from codes.types import CodePurpose
from config.outside_calls import EMAIL_PROVIDER, outside_call
from platform_admin.models import PlatformAdmin
from staff.models import Role, Staff

_MINUTES = int(CODE_LIFETIME.total_seconds() // 60)

_SUBJECTS = {
    CodePurpose.RESET: "Your code to reset your password at {office}",
    CodePurpose.JOIN: "Your code to join {office}",
}

_INTROS = {
    CodePurpose.RESET: "Use this code to choose a new password for {office}:",
    CodePurpose.JOIN: (
        "You've been added to {office}'s CRM as {role}. "
        "Open the sign-up page, enter your work email and this code, then choose your password:"
    ),
}

_ENDINGS = {
    CodePurpose.RESET: (
        "If you didn't ask for it, you can ignore this email. Your password stays the same."
    ),
    CodePurpose.JOIN: (
        "After that, ask your Admin to send a new one. "
        "If you weren't expecting it, you can ignore this email."
    ),
}


def send_code_email(
    staff: Staff, code: str, purpose: CodePurpose, office: str, link: str | None = None
) -> None:
    """Email `code` to `staff` as plain text (no HTML, so nothing typed can become markup).

    `link` is the page to enter it on (the sign-up page for an Invite).
    """
    role = Role(staff.role).label
    lines = [
        f"Hello {staff.name},",
        _INTROS[purpose].format(office=office, role=role),
        code,
        f"It works for {_MINUTES} minutes, once.",
        _ENDINGS[purpose],
    ]
    if link is not None:
        lines.insert(3, f"Sign-up page: {link}")
    with outside_call(EMAIL_PROVIDER, f"send_{purpose.value}_code"):
        send_mail(
            subject=_SUBJECTS[purpose].format(office=office),
            message="\n\n".join(lines),
            from_email=None,
            recipient_list=[str(staff.email)],
        )


PLATFORM_RESET_SUBJECT = "Your code to reset your Platform password"


def send_platform_code_email(admin: PlatformAdmin, code: str, platform_address: str) -> None:
    """Email a Platform Admin their reset `code` as plain text (issue 63).

    It names the Platform's address to enter it on, and never holds a link carrying the Code.
    Sent from DEFAULT_FROM_EMAIL.
    """
    lines = [
        f"Hello {admin.name},",
        f"Use this code to choose a new password for the Platform at {platform_address}:",
        code,
        f"It works for {_MINUTES} minutes, once. Enter it on the Platform's Forgot password page.",
        _ENDINGS[CodePurpose.RESET],
    ]
    with outside_call(EMAIL_PROVIDER, "send_platform_reset_code"):
        send_mail(
            subject=PLATFORM_RESET_SUBJECT,
            message="\n\n".join(lines),
            from_email=None,
            recipient_list=[str(admin.email)],
        )


PLATFORM_JOIN_SUBJECT = "Your code to join the Platform"


def send_platform_join_email(admin: PlatformAdmin, code: str, join_link: str) -> None:
    """Email an invited Platform Admin their join `code` and the Join page (issue 65).

    Plain text, from DEFAULT_FROM_EMAIL; the link never carries the Code.
    """
    lines = [
        f"Hello {admin.name},",
        "You've been added to the Platform as a Platform Admin. Open the Join page, enter your "
        "email and this code, then choose your password:",
        code,
        f"Join page: {join_link}",
        f"It works for {_MINUTES} minutes, once.",
        _ENDINGS[CodePurpose.JOIN],
    ]
    with outside_call(EMAIL_PROVIDER, "send_platform_join_code"):
        send_mail(
            subject=PLATFORM_JOIN_SUBJECT,
            message="\n\n".join(lines),
            from_email=None,
            recipient_list=[str(admin.email)],
        )
