"""How a Staff member's own name is tidied before it is saved (issue 38)."""

from __future__ import annotations

import pytest

from staff.my_settings import capitalise_name


@pytest.mark.parametrize(
    ("typed", "saved"),
    [
        ("sita", "Sita"),
        ("sita sharma", "Sita Sharma"),
        ("  sita   kumari\tsharma  ", "Sita Kumari Sharma"),
        ("Ram KC", "Ram KC"),
        ("ram mcDonald", "Ram McDonald"),
        ("éva lópez", "Éva López"),
        ("o'neil", "O'neil"),
        ("<b>sita</b>", "<b>sita</b>"),
        ("", ""),
    ],
)
def test_capitalises_the_first_letter_of_each_word_and_tidies_spaces(
    typed: str, saved: str
) -> None:
    """Each word starts with a capital; the rest of the word is kept as typed."""
    assert capitalise_name(typed) == saved
