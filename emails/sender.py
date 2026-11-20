"""The one sender (ADR 0023): every email goes out through the product's own SMTP account.

The account is set only in the environment (`EMAIL_HOST` and the others, `DEFAULT_FROM_EMAIL`,
`EMAIL_TIMEOUT`) and used through Django's mail interface, which tests replace with their
mailbox. An office's email is sent as `"<office name>" <DEFAULT_FROM_EMAIL>` with Reply-To set to
its contact email; a Platform email has no Reply-To. Nothing about the email is logged.
"""

from __future__ import annotations

import smtplib
from collections.abc import Sequence
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.utils import formataddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail.message import SafeMIMEMultipart, SafeMIMEText

from config.outside_calls import EMAIL_PROVIDER, outside_call
from emails.exceptions import SendFailedError
from emails.types import BuiltEmail, FileAttachment, InlineImage

_HTML = "text/html"


class _Email(EmailMultiAlternatives):
    """An email whose images (if any) belong with its HTML, beside any files sent with it.

    The text and HTML go in a "related" part with the images, so mail apps never list the
    images as files; the files sit beside that part, in the email's outer "mixed" part.
    """

    inline: Sequence[MIMEBase] = ()

    def _create_alternatives(self, msg: MIMEBase) -> MIMEBase:
        """Put the plain text with its HTML (Django calls this), and the images beside them."""
        encoding = self.encoding or settings.DEFAULT_CHARSET
        alternative = SafeMIMEMultipart(_subtype="alternative", encoding=encoding)
        alternative.attach(msg)
        for content, mimetype in self.alternatives:
            alternative.attach(SafeMIMEText(str(content), mimetype.split("/", 1)[1], encoding))
        if not self.inline:
            return alternative
        related = SafeMIMEMultipart(_subtype="related", encoding=encoding)
        related.attach(alternative)
        for part in self.inline:
            related.attach(part)
        return related


def sender_address(from_name: str) -> str:
    """The From header: the name on one line (quoted where it needs it), then our address."""
    return formataddr((" ".join(from_name.split()), settings.DEFAULT_FROM_EMAIL))


def _inline_part(image: InlineImage) -> MIMEImage:
    """An image shown inside the email by its content id, never loaded from a server."""
    part = MIMEImage(image.content, _subtype=image.content_type.split("/", 1)[1])
    part.add_header("Content-ID", f"<{image.content_id}>")
    part.add_header("Content-Disposition", "inline", filename=f"{image.content_id}")
    return part


def _message(email: BuiltEmail, to: str, files: Sequence[FileAttachment]) -> EmailMultiAlternatives:
    message = _Email(
        subject=email.subject,
        body=email.text,
        from_email=sender_address(email.from_name),
        to=[to],
        reply_to=[email.reply_to] if email.reply_to else None,
    )
    message.inline = [_inline_part(image) for image in email.inline_images]
    message.attach_alternative(email.html, _HTML)
    for file in files:
        message.attach(file.file_name, file.content, file.content_type)
    return message


def send_email(
    email: BuiltEmail,
    to: str,
    operation: str = "send_email",
    files: Sequence[FileAttachment] = (),
) -> None:
    """Send `email`, with `files` attached, to the one address `to`.

    `operation` names it in the outside-call log.

    Raises:
        SendFailedError: the mail server didn't take it; the reason never holds its words.
    """
    message = _message(email, to, files)
    with outside_call(EMAIL_PROVIDER, operation):
        try:
            message.send(fail_silently=False)
        except (smtplib.SMTPException, OSError) as error:
            # Only the kind of failure is kept: the server's words never reach a log.
            raise SendFailedError(cause=type(error).__name__) from None
