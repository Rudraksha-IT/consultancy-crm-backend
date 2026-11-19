"""The one sender (ADR 0023): every email goes out through the product's own SMTP account.

The account is set only in the environment (`EMAIL_HOST` and the others, `DEFAULT_FROM_EMAIL`,
`EMAIL_TIMEOUT`) and used through Django's mail interface, which tests replace with their
mailbox. An office's email is sent as `"<office name>" <DEFAULT_FROM_EMAIL>` with Reply-To set to
its contact email; a Platform email has no Reply-To. Nothing about the email is logged.
"""

from __future__ import annotations

import smtplib
from email.mime.image import MIMEImage
from email.utils import formataddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from config.outside_calls import EMAIL_PROVIDER, outside_call
from emails.exceptions import SendFailedError
from emails.types import BuiltEmail, InlineImage

_HTML = "text/html"


class _WithInlineImages(EmailMultiAlternatives):
    """An email whose images belong with its HTML.

    A "related" part keeps mail apps from listing the images as files.
    """

    mixed_subtype = "related"


def sender_address(from_name: str) -> str:
    """The From header: the name on one line (quoted where it needs it), then our address."""
    return formataddr((" ".join(from_name.split()), settings.DEFAULT_FROM_EMAIL))


def _inline_part(image: InlineImage) -> MIMEImage:
    """An image shown inside the email by its content id, never loaded from a server."""
    part = MIMEImage(image.content, _subtype=image.content_type.split("/", 1)[1])
    part.add_header("Content-ID", f"<{image.content_id}>")
    part.add_header("Content-Disposition", "inline", filename=f"{image.content_id}")
    return part


def _message(email: BuiltEmail, to: str) -> EmailMultiAlternatives:
    kind = _WithInlineImages if email.inline_images else EmailMultiAlternatives
    message = kind(
        subject=email.subject,
        body=email.text,
        from_email=sender_address(email.from_name),
        to=[to],
        reply_to=[email.reply_to] if email.reply_to else None,
    )
    message.attach_alternative(email.html, _HTML)
    for image in email.inline_images:
        message.attach(_inline_part(image))
    return message


def send_email(email: BuiltEmail, to: str) -> None:
    """Send `email` to the one address `to`.

    Raises:
        SendFailedError: the mail server didn't take it; the reason never holds its words.
    """
    message = _message(email, to)
    with outside_call(EMAIL_PROVIDER, "send_email"):
        try:
            message.send(fail_silently=False)
        except (smtplib.SMTPException, OSError) as error:
            # Only the kind of failure is kept: the server's words never reach a log.
            raise SendFailedError(cause=type(error).__name__) from None
