"""The email builder (ADR 0023): an email's content plus a sender look becomes a finished email.

Pure: no database or network work, so it is unit-tested and the demo command renders exactly
what production sends. Templates are Django templates with autoescaping on for HTML; nothing
Staff typed or taken from a record is ever marked safe. The plain-text copy uses the same words.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.template import Context, Engine

from emails.looks import PLATFORM_ACCENT, readable_accent
from emails.types import (
    BuiltEmail,
    CentralLogin,
    EmailContent,
    InlineImage,
    Logo,
    Message,
    OfficeDetails,
    OfficeLook,
    PlatformInvite,
    PlatformResetCode,
    SenderLook,
    StaffInviteCode,
    StaffResetCode,
    TestEmail,
)

CODE_MINUTES = 30
LOGO_CONTENT_ID = "logo"
# Mail apps show PNG and JPEG reliably; SVG and WebP are replaced by the name.
_INLINE_LOGO_TYPES = frozenset({"image/png", "image/jpeg"})
_LINK = re.compile(r"https?://[^\s\"'<>]+")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_LIST_ITEM = "- "

_TEMPLATES = Path(__file__).resolve().parent / "templates"
_HTML = Engine(dirs=[str(_TEMPLATES)], autoescape=True)
_TEXT = Engine(dirs=[str(_TEMPLATES)], autoescape=False)


@dataclass(frozen=True)
class _Body:
    """What one kind adds inside the frame: its subject line, body template and values."""

    subject: str
    template: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Block:
    """A paragraph (its lines joined by line breaks) or a list (lines starting "- ")."""

    lines: tuple[str, ...]
    is_list: bool


def _one_line(value: str) -> str:
    """Collapse every run of spaces and line breaks, so a header never spans two lines."""
    return " ".join(value.split())


def _link(value: str) -> str:
    """The link if it is a plain http(s) address, else nothing (it is then never a link)."""
    return value if _LINK.fullmatch(value) else ""


def _blocks(text: str) -> list[_Block]:
    blocks: list[_Block] = []
    for paragraph in _BLANK_LINE.split(text.replace("\r\n", "\n").strip()):
        lines = tuple(line.rstrip() for line in paragraph.strip("\n").split("\n"))
        if not any(lines):
            continue
        if all(line.startswith(_LIST_ITEM) for line in lines):
            blocks.append(_Block(tuple(line[len(_LIST_ITEM) :] for line in lines), is_list=True))
        else:
            blocks.append(_Block(lines, is_list=False))
    return blocks


# ---- One function per kind ----------------------------------------------------------------


def _message(_: str, email: Message) -> _Body:
    return _Body(email.subject, "message", {"blocks": _blocks(email.text), "text": email.text})


def _staff_invite(brand: str, email: StaffInviteCode) -> _Body:
    return _Body(
        f"Your code to join {brand}",
        "staff_invite",
        {"name": email.name, "role": email.role, "code": email.code, "office": brand,
         "link": _link(email.signup_link)},
    )  # fmt: skip


def _staff_reset(brand: str, email: StaffResetCode) -> _Body:
    return _Body(
        f"Your code to reset your password at {brand}",
        "staff_reset",
        {"name": email.name, "code": email.code, "office": brand},
    )


def _platform_invite(_: str, email: PlatformInvite) -> _Body:
    return _Body(
        "Your code to join the Platform",
        "platform_invite",
        {"name": email.name, "code": email.code, "link": _link(email.join_link),
         "link_text": email.join_link},
    )  # fmt: skip


def _platform_reset(_: str, email: PlatformResetCode) -> _Body:
    return _Body(
        "Your code to reset your Platform password",
        "platform_reset",
        {"name": email.name, "code": email.code, "address": email.platform_address},
    )


def _central_login(_: str, email: CentralLogin) -> _Body:
    offices = [{"name": o.name, "link": _link(o.login_link)} for o in email.offices]
    return _Body("Your offices' login pages", "central_login", {"offices": offices})


def _test_email(brand: str, email: TestEmail) -> _Body:
    return _Body(f"Test email from {brand}", "test_email", {"name": email.name, "brand": brand})


_KINDS: dict[type, Callable[[str, Any], _Body]] = {
    Message: _message,
    StaffInviteCode: _staff_invite,
    StaffResetCode: _staff_reset,
    PlatformInvite: _platform_invite,
    PlatformResetCode: _platform_reset,
    CentralLogin: _central_login,
    TestEmail: _test_email,
}

# ---- The frame ----------------------------------------------------------------------------


def _footer_lines(details: OfficeDetails) -> list[str]:
    phone = f"Phone {details.phone}" if details.phone else ""
    lines = [details.address_line1, details.address_line2, phone, details.website]
    return [line for line in (*lines, details.contact_email) if line]


def _inline_logo(logo: Logo | None) -> InlineImage | None:
    if logo is None or logo.content_type not in _INLINE_LOGO_TYPES:
        return None
    return InlineImage(LOGO_CONTENT_ID, logo.content, logo.content_type)


def _frame(look: SenderLook) -> dict[str, Any]:
    """The values the frame needs: name, colour, logo and footer."""
    if isinstance(look, OfficeLook):
        accent, details = readable_accent(look.accent), look.details
    else:
        accent, details = PLATFORM_ACCENT, OfficeDetails()
    return {
        "brand": look.name,
        "accent": accent,
        "footer_lines": _footer_lines(details),
        "contact_email": details.contact_email,
        "minutes": CODE_MINUTES,
    }


def _footer_text(values: dict[str, Any]) -> str:
    reply = "This email comes from an address that can't receive replies."
    if values["contact_email"]:
        reply += f" To answer, write to {values['contact_email']}."
    return "\n".join([values["brand"], *values["footer_lines"], "", reply])


def build_email(look: SenderLook, content: EmailContent) -> BuiltEmail:
    """Return `content` as a finished email in `look`: one-line subject, HTML and plain text.

    Office emails carry the office's name, readable Theme colour, logo (PNG or JPEG only, inside
    the email) and Office details, with Reply-To set to the contact email. Platform emails use
    the product's name and emerald, with no Reply-To.
    """
    brand = _one_line(look.name)
    body = _KINDS[type(content)](brand, content)
    logo = _inline_logo(look.logo)
    values = {
        **_frame(look),
        **body.values,
        "brand": brand,
        "subject": _one_line(body.subject),
        "logo_cid": logo.content_id if logo else "",
        "body_template": f"emails/{body.template}.html",
    }
    text_body = (
        _TEXT.get_template(f"emails/{body.template}.txt")
        .render(Context(values, autoescape=False))
        .strip()
    )
    text = f"{brand}\n\n{text_body}\n\n--\n{_footer_text(values)}\n"
    html = _HTML.get_template("emails/frame.html").render(Context(values, autoescape=True))
    return BuiltEmail(
        subject=values["subject"],
        html=html,
        text=text,
        from_name=brand,
        reply_to=values["contact_email"] or None,
        inline_images=(logo,) if logo else (),
    )
