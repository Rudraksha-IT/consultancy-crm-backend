"""The stored date display setting, read through the tenancy services."""

from __future__ import annotations

import pytest

from tenancy.models import Consultancy
from tenancy.services import get_date_display
from tenancy.types import DateDisplay


class TestGetDateDisplay:
    @pytest.mark.parametrize(
        ("stored", "shown"),
        [
            ("AD", DateDisplay.AD),
            ("BS", DateDisplay.BS),
            ("", DateDisplay.AD),
            ("XX", DateDisplay.AD),
        ],
    )
    def test_a_damaged_stored_value_falls_back_on_ad(self, stored: str, shown: DateDisplay) -> None:
        consultancy = Consultancy(name="ABC Education", schema_name="abcedu", date_display=stored)

        assert get_date_display(consultancy) == shown

    def test_a_new_consultancy_shows_ad(self) -> None:
        consultancy = Consultancy(name="ABC Education", schema_name="abcedu")

        assert get_date_display(consultancy) == DateDisplay.AD
