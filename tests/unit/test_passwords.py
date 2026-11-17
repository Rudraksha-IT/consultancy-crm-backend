"""The password rules every Staff password must meet (spec 18c)."""

from __future__ import annotations

import pytest

from staff.passwords import PasswordRule, unmet_password_rules


class TestPasswordRules:
    def test_a_password_meeting_every_rule_has_none_unmet(self) -> None:
        assert unmet_password_rules("Kathmandu!2026") == []

    def test_an_empty_password_meets_no_rule(self) -> None:
        assert unmet_password_rules("") == list(PasswordRule)

    @pytest.mark.parametrize(
        ("password", "unmet"),
        [
            ("Ab1!xyz", [PasswordRule.MIN_LENGTH]),
            ("kathmandu!2026", [PasswordRule.CAPITAL]),
            ("KATHMANDU!2026", [PasswordRule.SMALL]),
            ("Kathmandu!!!!", [PasswordRule.NUMBER]),
            ("Kathmandu2026", [PasswordRule.SYMBOL]),
        ],
    )
    def test_each_rule_is_reported_on_its_own(
        self, password: str, unmet: list[PasswordRule]
    ) -> None:
        assert unmet_password_rules(password) == unmet

    def test_exactly_eight_characters_is_long_enough(self) -> None:
        assert unmet_password_rules("Abcdef1!") == []

    def test_a_space_counts_as_a_symbol(self) -> None:
        assert unmet_password_rules("Kathmandu 2026") == []

    def test_rules_are_reported_in_a_fixed_order(self) -> None:
        assert unmet_password_rules("!") == [
            PasswordRule.MIN_LENGTH,
            PasswordRule.CAPITAL,
            PasswordRule.SMALL,
            PasswordRule.NUMBER,
        ]

    def test_rule_names_are_what_the_api_returns(self) -> None:
        assert [rule.value for rule in PasswordRule] == [
            "min_length",
            "capital",
            "small",
            "number",
            "symbol",
        ]
