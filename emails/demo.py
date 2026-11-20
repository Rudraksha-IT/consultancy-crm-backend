"""The email demo (spec stories 94 to 97): every email kind, rendered by the real builder.

Only made-up names: the office is "Himal Education" and the Students are invented. The page is one
self-contained HTML file (no outside files), for the team and the Consultancy to check.
"""

from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from django.template import Context, Engine

from emails.builder import build_email
from emails.drafts import draft_email
from emails.looks import PRODUCT_NAME
from emails.types import (
    BuiltEmail,
    CentralLogin,
    ClassDetails,
    DocumentsNeeded,
    DraftContext,
    EmailContent,
    FeeLine,
    FeeReminder,
    LoginOffice,
    Logo,
    Message,
    MissingDocument,
    OfferReceived,
    OfficeDetails,
    OfficeLook,
    PaymentReceived,
    PlainEmail,
    PlatformInvite,
    PlatformLook,
    PlatformResetCode,
    ReadyMade,
    StaffInviteCode,
    StaffResetCode,
    TestEmail,
    VisaResult,
    Welcome,
)

DEMO_OFFICE = "Himal Education"
DEMO_FROM = "no-reply@crm.example"
OFFICE_GROUP = "Office emails"
PLATFORM_GROUP = "Platform emails"
_COUNSELLOR = "Sita Karki"
_STUDENT_EMAIL = "anjali.sharma@mail.example"
_CODE = "482913"
_TEMPLATES = Path(__file__).resolve().parent / "templates"
_PAGE = Engine(dirs=[str(_TEMPLATES)], autoescape=True)

DETAILS = OfficeDetails(
    contact_email="hello@himal-education.example",
    phone="+977 1 4412345",
    address_line1="2nd floor, Putalisadak",
    address_line2="Kathmandu 44600, Nepal",
    website="https://himal-education.example",
)


@dataclass(frozen=True)
class DemoEmail:
    """One email on the demo page: its name, group, who it goes to, and the email itself."""

    title: str
    group: str
    to: str
    kind: type
    email: BuiltEmail


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        body = name + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + row for row in rows)
    return b"".join(
        [b"\x89PNG\r\n\x1a\n", chunk(b"IHDR", header), chunk(b"IDAT", zlib.compress(raw)),
         chunk(b"IEND", b"")]
    )  # fmt: skip


def demo_logo() -> bytes:
    """A made-up PNG logo: two mountain peaks on white (no image library needed)."""
    width, height = 120, 44
    peaks = ((34, 6, 30), (70, 14, 26))  # centre x, top y, half width at the base
    ink, snow, white = b"\x1d\x4e\xd8", b"\xdb\xe6\xfb", b"\xff\xff\xff"
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            pixel = white
            for cx, top, half in peaks:
                reach = (y - top) * half / (height - 4 - top)
                if top <= y <= height - 4 and abs(x - cx) <= reach:
                    pixel = snow if y < top + 8 else ink
            row += pixel
        rows.append(bytes(row))
    return _png(width, height, rows)


def _office(logo: Logo | None) -> OfficeLook:
    return OfficeLook(name=DEMO_OFFICE, accent="#1d4ed8", logo=logo, details=DETAILS)


_ONE = DraftContext(first_name="Anjali", sender_name=_COUNSELLOR, office_name=DEMO_OFFICE)
_MANY = DraftContext(first_name=None, sender_name=_COUNSELLOR, office_name=DEMO_OFFICE)

_READY_MADE: tuple[tuple[str, DraftContext, ReadyMade], ...] = (
    ("Welcome", _ONE, Welcome(counsellor_name=_COUNSELLOR)),
    (
        "Documents needed",
        _ONE,
        DocumentsNeeded(
            documents=(
                MissingDocument(
                    "Bank statement", "Last 6 months, with the bank's stamp on every page"
                ),
                MissingDocument("Sponsor letter", "Signed by your sponsor, on plain paper"),
                MissingDocument("Grade 12 transcript", None),
            ),
            has_examples=True,
        ),
    ),
    (
        "Payment received",
        _ONE,
        PaymentReceived(40000, "Visa fee", None, "18 Nov 2026", "Bank transfer"),
    ),
    (
        "Fee reminder",
        _ONE,
        FeeReminder(
            fees=(
                FeeLine("Application fee", None, 25000),
                FeeLine("Visa fee", None, 40000),
                FeeLine("IELTS class", None, 12000),
            ),
            total=77000,
            paid=50000,
            still_to_pay=27000,
        ),
    ),
    (
        "Offer received",
        _ONE,
        OfferReceived(
            "Southern Cross University",
            "Master of Information Technology",
            "Feb 2027",
            conditional=True,
        ),
    ),
    ("Visa result: granted", _ONE, VisaResult(country="Australia", granted=True)),
    ("Visa result: refused", _ONE, VisaResult(country="Australia", granted=False)),
    (
        "Class details (to many Students)",
        _MANY,
        ClassDetails(
            "IELTS Morning", "Sunday to Friday", "7:00 to 8:30 am", "1 Dec 2026", "Hari Adhikari"
        ),
    ),
    ("Plain email", _ONE, PlainEmail()),
)


def _plain_example(text: str) -> str:
    return text.replace(
        "\n\n\n\n", "\n\nI've booked you in for Thursday at 2 pm. See you then.\n\n"
    )


def _office_emails(logo: Logo) -> list[DemoEmail]:
    emails = []
    for title, context, ready in _READY_MADE:
        draft = draft_email(context, ready)
        subject = draft.subject or "Your appointment on Thursday"
        text = _plain_example(draft.text)
        to = "each Student, one by one" if context.first_name is None else _STUDENT_EMAIL
        email = build_email(_office(logo), Message(subject, text))
        emails.append(DemoEmail(title, OFFICE_GROUP, to, type(ready), email))
    welcome = draft_email(_ONE, Welcome(counsellor_name=_COUNSELLOR))
    svg = build_email(
        _office(Logo(b"<svg/>", "image/svg+xml")), Message(welcome.subject, welcome.text)
    )
    emails.append(DemoEmail("Welcome (SVG logo: the name shows)", OFFICE_GROUP, _STUDENT_EMAIL,
                            Welcome, svg))  # fmt: skip
    return emails


def _account_emails(logo: Logo) -> list[DemoEmail]:
    office, platform = _office(logo), PlatformLook(name=PRODUCT_NAME, logo=None)
    staff = "ram.thapa@himal-education.example"
    admin = "asha.rai@platform.example"
    entries: tuple[tuple[str, str, str, OfficeLook | PlatformLook, EmailContent], ...] = (
        ("Staff Invite Code", OFFICE_GROUP, staff, office,
         StaffInviteCode("Ram Thapa", "Counsellor", _CODE, "https://himal.crm.example/join")),
        ("Staff password reset Code", OFFICE_GROUP, staff, office,
         StaffResetCode("Ram Thapa", _CODE)),
        ("Central login list", PLATFORM_GROUP, staff, platform,
         CentralLogin((LoginOffice(DEMO_OFFICE, "https://himal.crm.example/login?email=ram"),
                       LoginOffice("Everest Study Centre",
                                   "https://everest.crm.example/login?email=ram")))),
        ("Platform Admin Invite Code", PLATFORM_GROUP, admin, platform,
         PlatformInvite("Asha Rai", _CODE, "https://platform.crm.example/join")),
        ("Platform Admin password reset Code", PLATFORM_GROUP, admin, platform,
         PlatformResetCode("Asha Rai", _CODE, "platform.crm.example")),
        ("Platform test email", PLATFORM_GROUP, admin, platform, TestEmail("Asha Rai")),
    )  # fmt: skip
    return [
        DemoEmail(title, group, to, type(content), build_email(look, content))
        for title, group, to, look, content in entries
    ]


def demo_emails() -> list[DemoEmail]:
    """Every email kind, office ones with a PNG logo, plus one with an SVG logo."""
    logo = Logo(demo_logo(), "image/png")
    return [*_office_emails(logo), *_account_emails(logo)]


def _for_browser(email: BuiltEmail) -> str:
    """The HTML as a browser shows it: inline images become data addresses (demo only)."""
    html = email.html
    for image in email.inline_images:
        data = base64.b64encode(image.content).decode("ascii")
        html = html.replace(f"cid:{image.content_id}", f"data:{image.content_type};base64,{data}")
    return html


def render_demo_page(emails: list[DemoEmail]) -> str:
    """One self-contained page: the email list, the email in a frame, widths and looks."""
    items = [
        {
            "index": i,
            "title": e.title,
            "group": e.group,
            "to": e.to,
            "from_name": e.email.from_name,
            "reply_to": e.email.reply_to or "",
            "subject": e.email.subject,
            "html": _for_browser(e.email),
            "text": e.email.text,
        }
        for i, e in enumerate(emails)
    ]
    values = {"items": items, "office": DEMO_OFFICE, "from_address": DEMO_FROM,
              "office_group": OFFICE_GROUP, "platform_group": PLATFORM_GROUP}  # fmt: skip
    return _PAGE.get_template("emails/demo/page.html").render(Context(values, autoescape=True))
