"""Two-step login's pure parts (issue 61): secrets, the setup URI, numbers and recovery codes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import pyotp
import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from config.two_factor import TwoFactorSettings, read_two_factor_settings
from platform_admin.models import DEFAULT_PRODUCT_NAME as ISSUER
from two_factor.numbers import (
    RECOVERY_CODE_COUNT,
    check_number,
    is_number_shaped,
    is_recovery_code_shaped,
    new_recovery_codes,
    new_secret,
    recovery_code_hash,
    setup_uri,
    step_at,
)
from two_factor.sealing import open_secret, seal_secret

NOW = datetime(2026, 11, 6, 9, 0, 0, tzinfo=UTC)
STEP = timedelta(seconds=30)
GOOD_KEY = Fernet.generate_key().decode()


def number_at(secret: str, moment: datetime) -> str:
    """The number an authenticator app shows at `moment`."""
    return pyotp.TOTP(secret).at(moment)


class TestSecretsAndTheSetupUri:
    def test_a_secret_is_160_bits_of_base32(self) -> None:
        secret = new_secret()

        assert len(secret) == 32
        assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")

    def test_every_secret_is_new(self) -> None:
        assert len({new_secret() for _ in range(20)}) == 20

    def test_the_uri_names_the_product_and_the_email(self) -> None:
        uri = setup_uri("JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", "ops@platform.test", ISSUER)

        parsed = urlparse(uri)
        query = parse_qs(parsed.query)
        assert parsed.scheme == "otpauth"
        assert parsed.netloc == "totp"
        assert unquote(parsed.path) == f"/{ISSUER}:ops@platform.test"
        assert query["secret"] == ["JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"]
        assert query["issuer"] == [ISSUER]


class TestCheckNumber:
    def test_the_current_number_is_accepted_with_its_step(self) -> None:
        secret = new_secret()

        result = check_number(secret, number_at(secret, NOW), at=NOW, last_step=None)

        assert result.accepted_step == step_at(NOW)
        assert not result.replayed

    @pytest.mark.parametrize("offset", [-1, 1])
    def test_one_step_either_side_is_accepted(self, offset: int) -> None:
        secret = new_secret()

        result = check_number(
            secret, number_at(secret, NOW + offset * STEP), at=NOW, last_step=None
        )

        assert result.accepted_step == step_at(NOW) + offset

    @pytest.mark.parametrize("offset", [-3, -2, 2, 3])
    def test_numbers_outside_the_window_are_refused(self, offset: int) -> None:
        secret = new_secret()
        number = number_at(secret, NOW + offset * STEP)
        if number in {number_at(secret, NOW + n * STEP) for n in (-1, 0, 1)}:
            pytest.skip("the far number happens to equal a near one")

        result = check_number(secret, number, at=NOW, last_step=None)

        assert result.accepted_step is None

    def test_a_wrong_number_is_refused(self) -> None:
        secret = new_secret()
        wrong = f"{(int(number_at(secret, NOW)) + 1) % 1_000_000:06d}"

        assert check_number(secret, wrong, at=NOW, last_step=None).accepted_step is None

    def test_a_number_already_used_is_refused_as_replayed(self) -> None:
        secret = new_secret()
        number = number_at(secret, NOW)

        result = check_number(secret, number, at=NOW, last_step=step_at(NOW))

        assert result.accepted_step is None
        assert result.replayed

    def test_an_older_step_than_the_last_used_is_refused(self) -> None:
        secret = new_secret()
        earlier = number_at(secret, NOW - STEP)

        result = check_number(secret, earlier, at=NOW, last_step=step_at(NOW))

        assert result.accepted_step is None
        assert result.replayed

    def test_a_later_step_than_the_last_used_is_accepted(self) -> None:
        secret = new_secret()

        result = check_number(secret, number_at(secret, NOW + STEP), at=NOW, last_step=step_at(NOW))

        assert result.accepted_step == step_at(NOW) + 1

    @pytest.mark.parametrize(
        "number",
        ["", "12345", "1234567", "12345a", " 123456", "\uff11\uff12\uff13\uff14\uff15\uff16"],
    )
    def test_anything_but_six_digits_is_refused(self, number: str) -> None:
        assert check_number(new_secret(), number, at=NOW, last_step=None).accepted_step is None


class TestShapes:
    @pytest.mark.parametrize("text", ["000000", "123456"])
    def test_six_ascii_digits_are_a_number(self, text: str) -> None:
        assert is_number_shaped(text)

    @pytest.mark.parametrize(
        "text", ["", "12345", "1234567", "abcdef", "12 456", "١٢٣٤٥٦", "<b>1</b>", "1' OR 1=1"]
    )
    def test_anything_else_is_not(self, text: str) -> None:
        assert not is_number_shaped(text)

    def test_recovery_codes_have_their_own_shape(self) -> None:
        assert is_recovery_code_shaped("abcd-efgh-jkmn")
        assert is_recovery_code_shaped(" ABCD EFGH JKMN ")
        assert not is_recovery_code_shaped("abcd-efgh")
        assert not is_recovery_code_shaped("<script>-x")
        assert not is_recovery_code_shaped("a" * 200)


class TestRecoveryCodes:
    def test_ten_different_codes_in_three_groups_of_four(self) -> None:
        codes = new_recovery_codes()

        assert len(codes) == RECOVERY_CODE_COUNT == 10
        assert len(set(codes)) == 10
        for code in codes:
            groups = code.split("-")
            assert [len(group) for group in groups] == [4, 4, 4]
            assert is_recovery_code_shaped(code)

    def test_the_hash_is_not_the_code_and_ignores_case_spaces_and_hyphens(self) -> None:
        code = new_recovery_codes()[0]

        hashed = recovery_code_hash(code)

        assert code not in hashed
        assert recovery_code_hash(code.upper().replace("-", " ")) == hashed
        assert recovery_code_hash(new_recovery_codes()[0]) != hashed


class TestSealing:
    def test_a_sealed_secret_opens_with_the_same_key_and_never_shows_it(self) -> None:
        with override_settings(TWO_FACTOR_SECRETS_KEY=GOOD_KEY):
            sealed = seal_secret("JBSWY3DPEHPK3PXP")

            assert "JBSWY3DPEHPK3PXP" not in sealed
            assert open_secret(sealed) == "JBSWY3DPEHPK3PXP"

    def test_another_key_cant_open_it(self) -> None:
        with override_settings(TWO_FACTOR_SECRETS_KEY=GOOD_KEY):
            sealed = seal_secret("JBSWY3DPEHPK3PXP")
        with (
            override_settings(TWO_FACTOR_SECRETS_KEY=Fernet.generate_key().decode()),
            pytest.raises(ValueError, match="can't be opened"),
        ):
            open_secret(sealed)

    def test_no_key_means_no_sealing(self) -> None:
        with (
            override_settings(TWO_FACTOR_SECRETS_KEY=""),
            pytest.raises(ImproperlyConfigured, match="TWO_FACTOR_SECRETS_KEY"),
        ):
            seal_secret("JBSWY3DPEHPK3PXP")


class TestReadTwoFactorSettings:
    def test_defaults_to_off_with_no_key(self) -> None:
        assert read_two_factor_settings({}) == TwoFactorSettings(
            platform_admin_default=False, staff_default=False, secrets_key=""
        )

    @pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
    def test_reads_the_default(self, value: str, expected: bool) -> None:
        environ = {"PLATFORM_ADMIN_TWO_FACTOR_DEFAULT": value, "TWO_FACTOR_SECRETS_KEY": GOOD_KEY}

        assert read_two_factor_settings(environ).platform_admin_default is expected

    @pytest.mark.parametrize("value", ["", "yes", "1", "True", "on", "false "])
    def test_refuses_anything_but_true_or_false(self, value: str) -> None:
        with pytest.raises(ImproperlyConfigured, match="PLATFORM_ADMIN_TWO_FACTOR_DEFAULT"):
            read_two_factor_settings({"PLATFORM_ADMIN_TWO_FACTOR_DEFAULT": value})

    @pytest.mark.parametrize(
        "key", ["short", "x" * 44, "not a fernet key at all, not even close!!!"]
    )
    def test_refuses_a_key_that_isnt_a_fernet_key(self, key: str) -> None:
        with pytest.raises(ImproperlyConfigured, match="TWO_FACTOR_SECRETS_KEY"):
            read_two_factor_settings({"TWO_FACTOR_SECRETS_KEY": key})

    def test_refuses_two_step_login_on_by_default_without_a_key(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="TWO_FACTOR_SECRETS_KEY"):
            read_two_factor_settings({"PLATFORM_ADMIN_TWO_FACTOR_DEFAULT": "true"})

    @pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
    def test_reads_the_staff_default(self, value: str, expected: bool) -> None:
        environ = {"STAFF_TWO_FACTOR_DEFAULT": value, "TWO_FACTOR_SECRETS_KEY": GOOD_KEY}

        settings = read_two_factor_settings(environ)

        assert settings.staff_default is expected
        assert settings.platform_admin_default is False

    @pytest.mark.parametrize("value", ["", "yes", "1", "True", "on", " true"])
    def test_refuses_a_staff_default_but_true_or_false(self, value: str) -> None:
        environ = {"STAFF_TWO_FACTOR_DEFAULT": value, "TWO_FACTOR_SECRETS_KEY": GOOD_KEY}

        with pytest.raises(ImproperlyConfigured) as refused:
            read_two_factor_settings(environ)

        assert str(refused.value) == "STAFF_TWO_FACTOR_DEFAULT must be true or false."

    def test_refuses_staff_two_step_login_on_by_default_without_a_key(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="TWO_FACTOR_SECRETS_KEY"):
            read_two_factor_settings({"STAFF_TWO_FACTOR_DEFAULT": "true"})

    def test_the_settings_never_show_the_key(self) -> None:
        settings = read_two_factor_settings({"TWO_FACTOR_SECRETS_KEY": GOOD_KEY})

        assert GOOD_KEY not in repr(settings)
