"""Emails that carry a Code (spec 2026-11-18 stories 23-27, ADR 0023).

A Staff member's Codes come in their office's look (its name as sender, logo, Theme colour and
Office details); a Platform Admin's in the Platform's look. They never need the office's contact
email, and the Code is never put in a link or button.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codes.types import CodePurpose
from emails.builder import build_email
from emails.office import office_look
from emails.platform import platform_look
from emails.sender import send_email
from emails.types import (
    PlatformInvite,
    PlatformResetCode,
    StaffEmailChangeCode,
    StaffInviteCode,
    StaffResetCode,
)
from staff.models import Role, Staff

if TYPE_CHECKING:
    from platform_admin.models import PlatformAdmin
    from tenancy.models import Consultancy


def send_code_email(
    staff: Staff,
    code: str,
    purpose: CodePurpose,
    consultancy: Consultancy,
    link: str | None = None,
) -> None:
    """Email `code` to `staff` in `consultancy`'s look.

    `link` is the sign-up page for an Invite (shown as a button; it never carries the Code).

    Raises:
        SendFailedError: the mail server didn't take it.
    """
    if purpose == CodePurpose.JOIN:
        content: StaffInviteCode | StaffResetCode = StaffInviteCode(
            name=str(staff.name),
            role=Role(staff.role).label,
            code=code,
            signup_link=link or "",
        )
    else:
        content = StaffResetCode(name=str(staff.name), code=code)
    send_email(
        build_email(office_look(consultancy), content),
        str(staff.email),
        f"send_{purpose.value}_code",
    )


def send_email_change_code(
    staff: Staff, code: str, new_email: str, consultancy: Consultancy
) -> None:
    """Email `code` to the `new_email` `staff` is moving to, in `consultancy`'s look.

    Raises:
        SendFailedError: the mail server didn't take it.
    """
    content = StaffEmailChangeCode(name=str(staff.name), code=code, new_email=new_email)
    send_email(build_email(office_look(consultancy), content), new_email, "send_email_change_code")


def send_platform_code_email(admin: PlatformAdmin, code: str, platform_address: str) -> None:
    """Email a Platform Admin their reset `code`, naming the Platform's address to enter it on.

    Raises:
        SendFailedError: the mail server didn't take it.
    """
    content = PlatformResetCode(name=str(admin.name), code=code, platform_address=platform_address)
    send_email(build_email(platform_look(), content), str(admin.email), "send_platform_reset_code")


def send_platform_join_email(admin: PlatformAdmin, code: str, join_link: str) -> None:
    """Email an invited Platform Admin their join `code` and the Join page (never in the link).

    Raises:
        SendFailedError: the mail server didn't take it.
    """
    content = PlatformInvite(name=str(admin.name), code=code, join_link=join_link)
    send_email(build_email(platform_look(), content), str(admin.email), "send_platform_join_code")
