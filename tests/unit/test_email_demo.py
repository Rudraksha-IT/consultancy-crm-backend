"""The email demo command (spec stories 94 to 97): every kind, made-up names only."""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from django.core.management import call_command

from emails.demo import DEMO_OFFICE, demo_emails, demo_logo
from emails.types import EmailContent, Message, ReadyMade


def _render(tmp_path: Path) -> str:
    out = tmp_path / "demo.html"
    call_command("render_email_demo", output=str(out))
    return out.read_text(encoding="utf-8")


def test_every_kind_is_in_the_demo() -> None:
    """The eight ready-made emails, the Code emails, the Central login and the test email."""
    kinds = {email.kind for email in demo_emails()}
    every = {*get_args(ReadyMade.__value__), *get_args(EmailContent.__value__)} - {Message}
    assert every <= kinds


def test_the_page_shows_every_email(tmp_path: Path) -> None:
    """The command writes one page holding each email, in its own frame."""
    page = _render(tmp_path)
    for email in demo_emails():
        assert email.email.subject.replace("'", "&#x27;") in page
    assert page.count('class="src-html"') == len(demo_emails())
    assert DEMO_OFFICE in page


def test_the_page_is_self_contained(tmp_path: Path) -> None:
    """Nothing is loaded from outside; the inline logo is carried in the page."""
    page = _render(tmp_path)
    assert re.search(r"<(script|img|link)[^>]+(src|href)=\"https?:", page) is None
    assert "cid:" not in page
    assert "data:image/png;base64," in page


def test_phone_and_computer_widths_and_both_looks(tmp_path: Path) -> None:
    """The page switches widths and between office and Platform emails."""
    page = _render(tmp_path)
    for control in ['data-width="phone"', 'data-width="computer"', "Office emails",
                    "Platform emails"]:  # fmt: skip
        assert control in page


def test_the_logo_is_a_png() -> None:
    """The made-up logo is a real PNG, so it shows the inline-logo path."""
    assert demo_logo().startswith(b"\x89PNG\r\n\x1a\n")
