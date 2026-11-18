"""What the email builder takes and gives back (ADR 0023): plain values only, no models."""

from __future__ import annotations

from dataclasses import dataclass

# ---- Who the email comes from -------------------------------------------------------------


@dataclass(frozen=True)
class Logo:
    """An office's or the product's logo file, as stored (checked when it was uploaded)."""

    content: bytes
    content_type: str


@dataclass(frozen=True)
class OfficeDetails:
    """The office's details for the footer (Configuration → General). Empty ones are left out."""

    contact_email: str = ""
    phone: str = ""
    address_line1: str = ""
    address_line2: str = ""
    website: str = ""


@dataclass(frozen=True)
class OfficeLook:
    """An office's email: its name, logo, Theme main colour and Office details."""

    name: str
    accent: str
    logo: Logo | None
    details: OfficeDetails


@dataclass(frozen=True)
class PlatformLook:
    """A Platform email: the product's name and logo, in emerald."""

    name: str
    logo: Logo | None


type SenderLook = OfficeLook | PlatformLook

# ---- What the email says ------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    """A subject line and message as Staff send them (a ready-made email after editing)."""

    subject: str
    text: str


@dataclass(frozen=True)
class StaffInviteCode:
    """A Staff Invite: the Code and the sign-up page (the link never carries the Code)."""

    name: str
    role: str
    code: str
    signup_link: str


@dataclass(frozen=True)
class StaffResetCode:
    """A Staff password reset Code."""

    name: str
    code: str


@dataclass(frozen=True)
class PlatformInvite:
    """A Platform Admin Invite: the Code and the Join page."""

    name: str
    code: str
    join_link: str


@dataclass(frozen=True)
class PlatformResetCode:
    """A Platform Admin password reset Code, naming the Platform's address."""

    name: str
    code: str
    platform_address: str


@dataclass(frozen=True)
class LoginOffice:
    """One office in the Central login email: its name and its login page."""

    name: str
    login_link: str


@dataclass(frozen=True)
class CentralLogin:
    """The list of offices an email address belongs to."""

    offices: tuple[LoginOffice, ...]


@dataclass(frozen=True)
class TestEmail:
    """The Platform's test email, sent to the signed-in Platform Admin."""

    __test__ = False  # not a pytest test class, despite its name

    name: str


type EmailContent = (
    Message
    | StaffInviteCode
    | StaffResetCode
    | PlatformInvite
    | PlatformResetCode
    | CentralLogin
    | TestEmail
)


@dataclass(frozen=True)
class InlineImage:
    """An image carried inside the email, shown by its content id (never loaded from a server)."""

    content_id: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class BuiltEmail:
    """A finished email, ready for the sender: one-line subject, HTML and a plain-text copy."""

    subject: str
    html: str
    text: str
    from_name: str
    reply_to: str | None
    inline_images: tuple[InlineImage, ...]


# ---- Ready-made emails: the starting text -------------------------------------------------


@dataclass(frozen=True)
class DraftContext:
    """Who it's to (no first name: an email to many, "Dear Student,") and who signs it."""

    first_name: str | None
    sender_name: str
    office_name: str


@dataclass(frozen=True)
class Welcome:
    """Welcome: names the Assigned Counsellor, if there is one."""

    counsellor_name: str | None


@dataclass(frozen=True)
class MissingDocument:
    """A Document still missing from the checklist, with its Document Example instruction."""

    name: str
    instruction: str | None


@dataclass(frozen=True)
class DocumentsNeeded:
    """Documents needed: the missing Documents, and whether examples are attached."""

    documents: tuple[MissingDocument, ...]
    has_examples: bool


@dataclass(frozen=True)
class PaymentReceived:
    """Payment received: one Payment's details; the date is already written for the office."""

    amount: int
    fee_type: str
    note: str | None
    paid_on: str
    method: str
    receipt_number: str


@dataclass(frozen=True)
class FeeLine:
    """One Fee in the Fee reminder."""

    fee_type: str
    note: str | None
    amount: int


@dataclass(frozen=True)
class FeeReminder:
    """Fee reminder: the Fees, what was paid and the one amount still to pay (the Balance)."""

    fees: tuple[FeeLine, ...]
    total: int
    paid: int
    still_to_pay: int


@dataclass(frozen=True)
class OfferReceived:
    """Offer received: one Offer Letter's University, Course and Intake."""

    university: str
    course: str
    intake: str
    conditional: bool


@dataclass(frozen=True)
class VisaResult:
    """Visa result: Granted or Refused."""

    country: str
    granted: bool


@dataclass(frozen=True)
class ClassDetails:
    """Class details: one Batch's name, days, time, start date and Teacher."""

    name: str
    days: str
    time: str
    starts: str
    teacher: str


@dataclass(frozen=True)
class PlainEmail:
    """Plain email: only the greeting and sign-off."""


type ReadyMade = (
    Welcome
    | DocumentsNeeded
    | PaymentReceived
    | FeeReminder
    | OfferReceived
    | VisaResult
    | ClassDetails
    | PlainEmail
)


@dataclass(frozen=True)
class Draft:
    """A ready-made email's starting subject line and message, as plain text for Staff to edit."""

    subject: str
    text: str
