"""The email builder (spec, Seam 2): every kind's subject line, HTML and plain text.

The builder is pure: it takes a sender look and plain values, and returns the finished email.
"""

from __future__ import annotations

import re

import pytest

from codes.services import CODE_LIFETIME
from emails.builder import CODE_MINUTES, build_email
from emails.drafts import draft_email
from emails.looks import PLATFORM_ACCENT, readable_accent
from emails.types import (
    BuiltEmail,
    CentralLogin,
    CheckingIn,
    ClassDetails,
    DocumentsNeeded,
    DraftContext,
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
    StaffEmailChangeCode,
    StaffEmailChanged,
    StaffInviteCode,
    StaffResetCode,
    TestEmail,
    VisaResult,
    Welcome,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff" + b"\x00" * 16
INJECTION = '<script>alert(1)</script><a href="https://evil.example">x</a> \' " {{ x }} & ;--'
CODE = "482913"

DETAILS = OfficeDetails(
    contact_email="hello@himal.example",
    phone="+977 1 4412345",
    address_line1="Putalisadak, Kathmandu",
    address_line2="Bagmati, Nepal",
    website="https://himal.example",
)
OFFICE = OfficeLook(name="Himal Education", accent="#7c3aed", logo=None, details=DETAILS)
PLATFORM = PlatformLook(name="Rudraksha CRM", logo=None)
CONTEXT = DraftContext(first_name="Anjali", sender_name="Sita Karki", office_name="Himal Education")
BULK = DraftContext(first_name=None, sender_name="Sita Karki", office_name="Himal Education")


def office(**changes: object) -> OfficeLook:
    """The test office with some values changed."""
    values: dict[str, object] = {
        "name": OFFICE.name,
        "accent": OFFICE.accent,
        "logo": OFFICE.logo,
        "details": OFFICE.details,
    }
    values.update(changes)
    return OfficeLook(**values)  # type: ignore[arg-type]


def message(text: str = "Dear Anjali,\n\nHello.", subject: str = "Hello") -> BuiltEmail:
    """A Staff-written email in the test office's look."""
    return build_email(OFFICE, Message(subject=subject, text=text))


class TestFrame:
    def test_office_email_shows_name_colour_and_footer(self) -> None:
        email = message()
        assert "Himal Education" in email.html
        assert readable_accent("#7c3aed") in email.html
        for value in ["hello@himal.example", "+977 1 4412345", "Putalisadak, Kathmandu"]:
            assert value in email.html
            assert value in email.text
        assert "Bagmati, Nepal" in email.html
        assert "https://himal.example" in email.text

    def test_footer_says_not_to_reply_and_names_the_contact_email(self) -> None:
        email = message()
        for part in (email.html, email.text):
            assert "can't receive replies" in part
            assert "write to hello@himal.example" in part

    def test_sender_name_and_reply_to(self) -> None:
        email = message()
        assert email.from_name == "Himal Education"
        assert email.reply_to == "hello@himal.example"

    def test_platform_email_uses_product_name_emerald_and_no_reply_to(self) -> None:
        email = build_email(PLATFORM, TestEmail(name="Asha Rai"))
        assert email.from_name == "Rudraksha CRM"
        assert email.reply_to is None
        assert PLATFORM_ACCENT in email.html
        assert readable_accent("#7c3aed") not in email.html

    def test_empty_office_details_are_left_out(self) -> None:
        bare = office(details=OfficeDetails(contact_email="hello@himal.example"))
        email = build_email(bare, Message(subject="Hi", text="Hello."))
        for part in (email.html, email.text):
            assert "Phone" not in part
            assert "None" not in part
        assert email.text.count("\n\n\n") == 0

    def test_without_contact_email_there_is_no_reply_to(self) -> None:
        email = build_email(office(details=OfficeDetails()), Message(subject="Hi", text="Hi."))
        assert email.reply_to is None
        assert "write to" not in email.text
        assert "can't receive replies" in email.text

    def test_sender_name_has_no_line_breaks(self) -> None:
        email = build_email(office(name="Himal\r\nBcc: x@y.z"), Message(subject="Hi", text="Hi"))
        assert "\n" not in email.from_name
        assert "\r" not in email.from_name

    def test_html_is_one_column_at_most_600_wide_and_dark_mode_aware(self) -> None:
        html = message().html
        assert "max-width:600px" in html
        assert 'name="color-scheme"' in html
        assert "prefers-color-scheme: dark" in html
        assert "<table" in html

    def test_no_image_is_loaded_from_a_server(self) -> None:
        html = build_email(office(logo=Logo(PNG, "image/png")), Message("Hi", "Hi")).html
        assert re.search(r"src=\"https?:", html) is None


class TestLogo:
    @pytest.mark.parametrize(("content", "kind"), [(PNG, "image/png"), (JPEG, "image/jpeg")])
    def test_png_or_jpeg_logo_is_carried_inside(self, content: bytes, kind: str) -> None:
        email = build_email(office(logo=Logo(content, kind)), Message("Hi", "Hi"))
        assert len(email.inline_images) == 1
        image = email.inline_images[0]
        assert image.content == content
        assert image.content_type == kind
        assert f'src="cid:{image.content_id}"' in email.html
        assert 'alt="Himal Education"' in email.html

    @pytest.mark.parametrize("kind", ["image/svg+xml", "image/webp"])
    def test_svg_or_webp_logo_becomes_the_office_name(self, kind: str) -> None:
        email = build_email(office(logo=Logo(b"<svg/>", kind)), Message("Hi", "Hi"))
        assert email.inline_images == ()
        assert "cid:" not in email.html
        assert "Himal Education" in email.html

    def test_no_logo_shows_the_name(self) -> None:
        email = message()
        assert email.inline_images == ()
        assert "<img" not in email.html


class TestEscaping:
    def test_staff_text_is_escaped_in_html_and_unchanged_in_plain_text(self) -> None:
        email = message(text=f"Dear Anjali,\n\n{INJECTION}", subject=f"Hi {INJECTION}")
        assert "<script>" not in email.html
        assert 'evil.example">' not in email.html
        assert "&lt;script&gt;" in email.html
        assert INJECTION in email.text
        assert "<script>" in email.subject

    def test_record_values_are_escaped(self) -> None:
        evil = office(
            name=INJECTION,
            details=OfficeDetails(contact_email="a@b.c", address_line1=INJECTION),
        )
        email = build_email(evil, Message("Hi", "Hi"))
        assert "<script>" not in email.html
        assert INJECTION in email.text

    def test_office_colour_must_be_a_hex_colour(self) -> None:
        email = build_email(office(accent="red;background:url(x)"), Message("Hi", "Hi"))
        assert "url(x)" not in email.html
        assert readable_accent(PLATFORM_ACCENT) in email.html

    def test_subject_line_is_kept_on_one_line(self) -> None:
        assert message(subject="Hi\r\nBcc: x@y.z").subject == "Hi Bcc: x@y.z"


class TestMessageLayout:
    def test_blank_lines_make_paragraphs_and_line_breaks_stay(self) -> None:
        html = message(text="One\ntwo\n\nThree").html
        assert re.search(r"<p[^>]*>One<br>two</p>", html)
        assert re.search(r"<p[^>]*>Three</p>", html)

    def test_lines_starting_with_a_dash_make_a_list(self) -> None:
        html = message(text="Bring:\n\n- Passport\n- Transcript").html
        assert re.search(r"<li[^>]*>Passport</li>", html)
        assert re.search(r"<li[^>]*>Transcript</li>", html)

    def test_plain_text_keeps_the_words_as_typed(self) -> None:
        text = message(text="One\ntwo\n\n- Passport").text
        assert "One\ntwo\n\n- Passport" in text


class TestCodeEmails:
    def test_staff_invite(self) -> None:
        email = build_email(
            OFFICE,
            StaffInviteCode(
                name="Ram Thapa",
                role="Counsellor",
                code=CODE,
                signup_link="https://himal.example.test/join",
            ),
        )
        assert email.subject == "Your code to join Himal Education"
        for part in (email.html, email.text):
            assert "Ram Thapa" in part
            assert "Counsellor" in part
            assert CODE in part
            assert "30 minutes" in part
            assert "weren't expecting" in part
        assert 'href="https://himal.example.test/join"' in email.html

    def test_staff_reset(self) -> None:
        email = build_email(OFFICE, StaffResetCode(name="Ram Thapa", code=CODE))
        assert email.subject == "Your code to reset your password at Himal Education"
        for part in (email.html, email.text):
            assert CODE in part
            assert "30 minutes" in part
            assert "didn't ask for it" in part

    def test_staff_email_change_code_names_the_new_email(self) -> None:
        email = build_email(
            OFFICE, StaffEmailChangeCode(name="Ram Thapa", code=CODE, new_email="ram@himal.example")
        )
        assert email.subject == "Your code to change your email at Himal Education"
        assert email.reply_to == "hello@himal.example"
        for part in (email.html, email.text):
            assert CODE in part
            assert "ram@himal.example" in part
            assert "30 minutes" in part
            assert "didn't ask for it" in part

    def test_staff_email_changed_notice_has_no_code(self) -> None:
        email = build_email(
            OFFICE, StaffEmailChanged(name="Ram Thapa", new_email="ram@himal.example")
        )
        assert email.subject == "Your email at Himal Education was changed"
        assert email.reply_to == "hello@himal.example"
        for part in (email.html, email.text):
            assert "Your email was changed to ram@himal.example." in part
            assert "If this wasn't you, tell your Admin." in part
        assert not re.search(r"\b\d{6}\b", email.text)

    def test_email_change_names_and_addresses_are_escaped(self) -> None:
        for content in (
            StaffEmailChangeCode(name=INJECTION, code=CODE, new_email=INJECTION),
            StaffEmailChanged(name=INJECTION, new_email=INJECTION),
        ):
            email = build_email(OFFICE, content)
            assert "<script>" not in email.html
            assert "&lt;script&gt;" in email.html

    def test_platform_reset_names_the_platform_address(self) -> None:
        email = build_email(
            PLATFORM,
            PlatformResetCode(name="Asha Rai", code=CODE, platform_address="platform.example"),
        )
        assert email.subject == "Your code to reset your Platform password"
        assert "platform.example" in email.text
        assert CODE in email.html

    def test_platform_invite(self) -> None:
        email = build_email(
            PLATFORM,
            PlatformInvite(name="Asha Rai", code=CODE, join_link="https://platform.example/join"),
        )
        assert email.subject == "Your code to join the Platform"
        assert CODE in email.text
        assert "https://platform.example/join" in email.text

    @pytest.mark.parametrize(
        "content",
        [
            StaffInviteCode("Ram", "Admin", CODE, "https://himal.example.test/join"),
            StaffResetCode("Ram", CODE),
            StaffEmailChangeCode("Ram", CODE, "ram@himal.example"),
            PlatformResetCode("Asha", CODE, "platform.example"),
            PlatformInvite("Asha", CODE, "https://platform.example/join"),
        ],
    )
    def test_no_code_is_ever_inside_a_link(
        self,
        content: StaffInviteCode
        | StaffResetCode
        | StaffEmailChangeCode
        | PlatformResetCode
        | PlatformInvite,
    ) -> None:
        look = PLATFORM if isinstance(content, PlatformResetCode | PlatformInvite) else OFFICE
        html = build_email(look, content).html
        for link in re.findall(r"<a\b[^>]*>.*?</a>", html, flags=re.DOTALL):
            assert CODE not in link

    def test_names_are_escaped(self) -> None:
        email = build_email(OFFICE, StaffResetCode(name=INJECTION, code=CODE))
        assert "<script>" not in email.html
        assert INJECTION in email.text


class TestCentralLogin:
    def test_each_office_has_its_name_and_a_button(self) -> None:
        email = build_email(
            PLATFORM,
            CentralLogin(
                offices=(
                    LoginOffice("Himal Education", "https://himal.example.test/login?email=a"),
                    LoginOffice(INJECTION, "https://other.example.test/login?email=a"),
                )
            ),
        )
        assert email.subject == "Your offices' login pages"
        assert 'href="https://himal.example.test/login?email=a"' in email.html
        assert "<script>" not in email.html
        assert "https://other.example.test/login?email=a" in email.text
        assert "didn't ask for this" in email.text

    def test_link_that_isnt_http_is_not_a_link(self) -> None:
        email = build_email(
            PLATFORM, CentralLogin(offices=(LoginOffice("X", "javascript:alert(1)"),))
        )
        assert "javascript:" not in email.html


class TestTestEmail:
    def test_says_email_is_working(self) -> None:
        email = build_email(PLATFORM, TestEmail(name="Asha Rai"))
        assert email.subject == "Test email from Rudraksha CRM"
        assert "email is working" in email.text
        assert "Asha Rai" in email.html


class TestDrafts:
    def test_welcome_greets_by_name_names_the_counsellor_and_signs_off(self) -> None:
        draft = draft_email(CONTEXT, Welcome(counsellor_name="Sita Karki"))
        assert draft.subject == "Welcome to Himal Education"
        assert draft.text.startswith("Dear Anjali,\n\n")
        assert "Your counsellor is Sita Karki" in draft.text
        assert draft.text.endswith("Kind regards,\nSita Karki\nHimal Education")

    def test_welcome_without_a_counsellor(self) -> None:
        draft = draft_email(CONTEXT, Welcome(counsellor_name=None))
        assert "give you a counsellor soon" in draft.text

    def test_documents_needed_lists_each_with_its_instruction(self) -> None:
        draft = draft_email(
            CONTEXT,
            DocumentsNeeded(
                documents=(
                    MissingDocument("Bank statement", "6 months, bank stamp on every page"),
                    MissingDocument("Passport", None),
                ),
                has_examples=True,
            ),
        )
        assert "- Bank statement: 6 months, bank stamp on every page" in draft.text
        assert "- Passport\n" in draft.text
        assert "attached examples" in draft.text

    def test_payment_received(self) -> None:
        draft = draft_email(
            CONTEXT,
            PaymentReceived(
                amount=40000,
                fee_type="Visa fee",
                note=None,
                paid_on="18 Nov 2026",
                method="Cash",
            ),
        )
        # No receipt numbers are shown anywhere for now (asked 2026-11-20).
        assert draft.subject == "Payment received"
        for value in ["NPR 40,000", "Visa fee", "18 Nov 2026", "Cash"]:
            assert value in draft.text
        assert "eceipt" not in draft.text

    def test_payment_note_follows_the_fee_type(self) -> None:
        draft = draft_email(
            CONTEXT,
            PaymentReceived(1000, "Other", "Courier", "18 Nov 2026", "Cash"),
        )
        assert "For: Other (Courier)" in draft.text

    def test_fee_reminder_lists_fees_paid_and_one_amount_left(self) -> None:
        draft = draft_email(
            CONTEXT,
            FeeReminder(
                fees=(FeeLine("Application fee", None, 25000), FeeLine("Visa fee", None, 40000)),
                total=65000,
                paid=30000,
                still_to_pay=35000,
            ),
        )
        for value in [
            "- Application fee: NPR 25,000",
            "Total fees: NPR 65,000",
            "Paid so far: NPR 30,000",
            "Still to pay: NPR 35,000",
        ]:
            assert value in draft.text

    @pytest.mark.parametrize(("conditional", "word"), [(True, "a conditional"), (False, "an unc")])
    def test_offer_received(self, *, conditional: bool, word: str) -> None:
        draft = draft_email(
            CONTEXT,
            OfferReceived("Deakin University", "Master of IT", "Feb 2027", conditional=conditional),
        )
        assert draft.subject == "Good news: an offer from Deakin University"
        assert word in draft.text
        assert "Master of IT" in draft.text
        assert "Feb 2027" in draft.text

    def test_visa_granted_and_refused_have_their_own_tone(self) -> None:
        granted = draft_email(CONTEXT, VisaResult(country="Australia", granted=True))
        refused = draft_email(CONTEXT, VisaResult(country="Australia", granted=False))
        assert granted.subject == "Your visa has been granted"
        assert "Congratulations" in granted.text
        assert refused.subject == "About your visa application"
        assert "sorry" in refused.text
        assert "Congratulations" not in refused.text

    def test_class_details(self) -> None:
        draft = draft_email(
            CONTEXT,
            ClassDetails("IELTS Morning", "Sunday to Friday", "7:00 to 8:30", "1 Dec 2026", "Hari"),
        )
        assert draft.subject == "Your class: IELTS Morning"
        for value in ["Sunday to Friday", "7:00 to 8:30", "1 Dec 2026", "Hari"]:
            assert value in draft.text

    def test_checking_in(self) -> None:
        draft = draft_email(CONTEXT, CheckingIn())
        assert draft.subject == "Checking in from Himal Education"
        assert draft.text.startswith("Dear Anjali,\n\nWe haven't heard from you in a while")

    def test_plain_email_has_only_greeting_and_sign_off(self) -> None:
        draft = draft_email(CONTEXT, PlainEmail())
        assert draft.subject == ""
        assert draft.text == "Dear Anjali,\n\n\n\nKind regards,\nSita Karki\nHimal Education"

    def test_many_students_get_a_general_greeting(self) -> None:
        draft = draft_email(BULK, PlainEmail())
        assert draft.text.startswith("Dear Student,")

    def test_draft_text_is_never_html(self) -> None:
        draft = draft_email(
            DraftContext(INJECTION, INJECTION, INJECTION), Welcome(counsellor_name=INJECTION)
        )
        assert f"Dear {INJECTION}," in draft.text
        assert "&lt;" not in draft.text

    def test_a_draft_becomes_an_email_in_the_frame(self) -> None:
        draft = draft_email(CONTEXT, Welcome(counsellor_name="Sita Karki"))
        email = build_email(OFFICE, Message(draft.subject, draft.text))
        assert "Your counsellor is Sita Karki" in email.html
        assert "hello@himal.example" in email.html


class TestReadableAccent:
    @pytest.mark.parametrize("colour", ["#047857", "#7c3aed", "#ffffff", "#fde047", "#000000"])
    def test_white_text_on_it_reaches_4_5_to_1(self, colour: str) -> None:
        assert _contrast_with_white(readable_accent(colour)) >= 4.5

    def test_a_dark_enough_colour_is_kept(self) -> None:
        assert readable_accent("#047857") == "#047857"

    def test_invalid_colour_falls_back_to_emerald(self) -> None:
        assert readable_accent("nope") == PLATFORM_ACCENT


def _contrast_with_white(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return 1.05 / (luminance + 0.05)


def test_code_lifetime_matches_the_codes() -> None:
    """The emails say how long a Code works; the same number the Codes use."""
    assert CODE_LIFETIME.total_seconds() == CODE_MINUTES * 60
