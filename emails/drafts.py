"""The eight ready-made emails' starting text (spec stories 28 to 46), the same for every office.

A draft is plain text: Staff edit it in the composer and the builder puts it in the frame when it
is sent. An email to many Students has no first name and starts "Dear Student,".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from emails.types import (
    ClassDetails,
    DocumentsNeeded,
    Draft,
    DraftContext,
    FeeReminder,
    OfferReceived,
    PaymentReceived,
    PlainEmail,
    ReadyMade,
    VisaResult,
    Welcome,
)
from payments.money import format_npr


def _for(fee_type: str, note: str | None) -> str:
    return f"{fee_type} ({note})" if note else fee_type


def _welcome(context: DraftContext, email: Welcome) -> Draft:
    office = context.office_name
    if email.counsellor_name:
        first = email.counsellor_name.split(" ")[0]
        who = (
            f"Your counsellor is {email.counsellor_name}. {first} will guide you through "
            "choosing a course, applying and getting your visa ready."
        )
    else:
        who = (
            "We'll give you a counsellor soon, who will guide you through choosing a course, "
            "applying and getting your visa ready."
        )
    return Draft(
        f"Welcome to {office}",
        f"Thank you for choosing {office} to help you study abroad. We're glad to have you "
        f"with us.\n\n{who}\n\nIf you have a question at any time, write to us or call the "
        "office. Our contact details are at the bottom of this email.",
    )


def _documents_needed(_: DraftContext, email: DocumentsNeeded) -> Draft:
    lines = "\n".join(
        f"- {d.name}: {d.instruction}" if d.instruction else f"- {d.name}" for d in email.documents
    )
    examples = (
        "\n\nWe've attached examples, so you can see what each one should look like."
        if email.has_examples
        else ""
    )
    return Draft(
        "Documents we still need from you",
        "To keep your application moving, please bring or send these documents:\n\n"
        f"{lines}{examples}\n\n"
        "You can bring them to the office, or email us clear scans.",
    )


def _payment_received(_: DraftContext, email: PaymentReceived) -> Draft:
    return Draft(
        f"Payment received: receipt {email.receipt_number}",
        "We have received your payment. Thank you.\n\n"
        f"Amount: {format_npr(email.amount)}\n"
        f"For: {_for(email.fee_type, email.note)}\n"
        f"Date: {email.paid_on}\n"
        f"Paid by: {email.method}\n"
        f"Receipt number: {email.receipt_number}\n\n"
        "Please keep this email as your record of the payment.",
    )


def _fee_reminder(_: DraftContext, email: FeeReminder) -> Draft:
    fees = "\n".join(f"- {_for(f.fee_type, f.note)}: {format_npr(f.amount)}" for f in email.fees)
    return Draft(
        "A reminder about your fees",
        f"This is a friendly reminder about your fees.\n\n{fees}\n\n"
        f"Total fees: {format_npr(email.total)}\n"
        f"Paid so far: {format_npr(email.paid)}\n"
        f"Still to pay: {format_npr(email.still_to_pay)}\n\n"
        "You can pay at the office. If you have already paid, please ignore this email, "
        "and thank you.",
    )


def _offer_received(_: DraftContext, email: OfferReceived) -> Draft:
    kind = "a conditional" if email.conditional else "an unconditional"
    conditions = ", including the conditions you still need to meet" if email.conditional else ""
    return Draft(
        f"Good news: an offer from {email.university}",
        f"Congratulations! {email.university} has made you {kind} offer.\n\n"
        f"University: {email.university}\nCourse: {email.course}\nIntake: {email.intake}\n\n"
        f"We'll talk you through the next steps{conditions}. Please get in touch to book a time.",
    )


def _visa_result(_: DraftContext, email: VisaResult) -> Draft:
    if email.granted:
        return Draft(
            "Your visa has been granted",
            f"Congratulations! Your visa for {email.country} has been granted.\n\n"
            "We're very happy for you. Next, we'll help you with flights, a place to stay and "
            "getting ready to leave. Please come and see us so we can plan the next steps "
            "together.",
        )
    return Draft(
        "About your visa application",
        f"We're sorry to tell you that your visa application for {email.country} was refused."
        "\n\nWe know this is disappointing. A refusal is not the end of your plans. We'll go "
        "through the reasons with you and look at your options together, including applying "
        "again or choosing another country. Please come and see us when you're ready.",
    )


def _class_details(_: DraftContext, email: ClassDetails) -> Draft:
    return Draft(
        f"Your class: {email.name}",
        "Here are the details of your class:\n\n"
        f"Class: {email.name}\nDays: {email.days}\nTime: {email.time}\n"
        f"Starts: {email.starts}\nTeacher: {email.teacher}\n\n"
        "Please come a few minutes early on the first day.",
    )


def _plain(_: DraftContext, __: PlainEmail) -> Draft:
    return Draft("", "")


_DRAFTS: dict[type, Callable[[DraftContext, Any], Draft]] = {
    Welcome: _welcome,
    DocumentsNeeded: _documents_needed,
    PaymentReceived: _payment_received,
    FeeReminder: _fee_reminder,
    OfferReceived: _offer_received,
    VisaResult: _visa_result,
    ClassDetails: _class_details,
    PlainEmail: _plain,
}


def draft_email(context: DraftContext, email: ReadyMade) -> Draft:
    """Return a ready-made email's starting subject line and message, filled in.

    The message is the greeting, the kind's middle (none for the Plain email) and the sign-off
    with the sender's name and office.
    """
    middle = _DRAFTS[type(email)](context, email)
    greeting = f"Dear {context.first_name}," if context.first_name else "Dear Student,"
    sign_off = f"Kind regards,\n{context.sender_name}\n{context.office_name}"
    return Draft(subject=middle.subject, text=f"{greeting}\n\n{middle.text}\n\n{sign_off}")
