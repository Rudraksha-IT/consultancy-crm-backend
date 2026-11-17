"""Signed-in devices are named "browser on system" from the browser's own description (issue 71).

The Next.js server sends the description and its own random value for "this browser"; Django reads
them only on a call with the internal token, and the name is for people only, never for security.
"""

from __future__ import annotations

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from auth_tokens.browsers import (
    BROWSER_AGENT_HEADER,
    BROWSER_KEY_HEADER,
    MAX_AGENT_LENGTH,
    device_name,
    login_browser,
)
from auth_tokens.models import MAX_DEVICE_NAME_LENGTH, UNKNOWN_DEVICE_NAME
from tests.conftest import TEST_INTERNAL_API_TOKEN

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
EDGE_WINDOWS = f"{CHROME_WINDOWS} Edg/130.0.2849.68"
OPERA_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 OPR/115.0.0.0"
)
FIREFOX_LINUX = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
SAFARI_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.1 Safari/605.1.15"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
CHROME_IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "CriOS/130.0.6723.90 Mobile/15E148 Safari/604.1"
)
FIREFOX_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) FxiOS/132.0 Mobile/15E148 Safari/605.1.15"
)
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.6723.102 Mobile Safari/537.36"
)
SAMSUNG_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile Safari/537.36"
)
CHROME_OS = (
    "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
BROWSER_KEY = "0123456789abcdef0123456789abcdef"


def _request(**headers: str) -> HttpRequest:
    request: HttpRequest = RequestFactory().post("/api/auth/login/", headers=headers)
    return request


def _trusted(**headers: str) -> HttpRequest:
    return _request(**{"X-Internal-Token": TEST_INTERNAL_API_TOKEN, **headers})


class TestDeviceName:
    @pytest.mark.parametrize(
        ("agent", "expected"),
        [
            (CHROME_WINDOWS, "Chrome on Windows"),
            (EDGE_WINDOWS, "Edge on Windows"),
            (OPERA_MAC, "Opera on macOS"),
            (FIREFOX_LINUX, "Firefox on Linux"),
            (SAFARI_MAC, "Safari on macOS"),
            (SAFARI_IPHONE, "Safari on iOS"),
            (CHROME_IPAD, "Chrome on iOS"),
            (FIREFOX_IPHONE, "Firefox on iOS"),
            (CHROME_ANDROID, "Chrome on Android"),
            (SAMSUNG_ANDROID, "Samsung Internet on Android"),
            (CHROME_OS, "Chrome on ChromeOS"),
        ],
    )
    def test_names_common_browsers_and_systems(self, agent: str, expected: str) -> None:
        assert device_name(agent) == expected

    def test_unknown_parts_say_so(self) -> None:
        assert device_name("curl/8.5.0") == "Unknown browser on Unknown system"
        assert device_name("Mozilla/5.0 (Windows NT 10.0) Strange/1.0") == (
            "Unknown browser on Windows"
        )
        assert device_name("Firefox/132.0") == "Firefox on Unknown system"

    def test_every_name_fits_the_stored_column(self) -> None:
        longest = device_name("SamsungBrowser/1")

        assert longest == "Samsung Internet on Unknown system"
        assert len(longest) <= MAX_DEVICE_NAME_LENGTH

    def test_nothing_sent_is_unknown(self) -> None:
        assert device_name("") == UNKNOWN_DEVICE_NAME

    def test_only_the_start_of_an_over_long_description_is_read(self) -> None:
        agent = "x" * MAX_AGENT_LENGTH + " Windows Chrome/130.0"

        assert device_name(agent) == UNKNOWN_DEVICE_NAME

    @pytest.mark.parametrize(
        "agent",
        [
            "<script>alert(1)</script> Chrome/1",
            "'; DROP TABLE auth_tokens_issuedrefreshtoken; -- Windows",
            "}body{background:url(javascript:alert(1))} Firefox/1",
        ],
    )
    def test_injection_shaped_text_never_reaches_the_name(self, agent: str) -> None:
        name = device_name(agent)

        for piece in ("<", ">", "'", ";", "{", "}", "script", "DROP", "javascript"):
            assert piece not in name


class TestLoginBrowser:
    def test_reads_the_key_and_name_on_a_call_with_the_internal_token(self) -> None:
        browser = login_browser(
            _trusted(**{BROWSER_KEY_HEADER: BROWSER_KEY, BROWSER_AGENT_HEADER: CHROME_WINDOWS})
        )

        assert browser.key == BROWSER_KEY
        assert browser.name == "Chrome on Windows"

    def test_trusts_nothing_without_the_internal_token(self) -> None:
        browser = login_browser(
            _request(**{BROWSER_KEY_HEADER: BROWSER_KEY, BROWSER_AGENT_HEADER: CHROME_WINDOWS})
        )

        assert browser.key is None
        assert browser.name == UNKNOWN_DEVICE_NAME

    @pytest.mark.parametrize(
        "key",
        ["", "abc", BROWSER_KEY.upper(), f"{BROWSER_KEY}0", f" {BROWSER_KEY[1:]}", "z" * 32],
    )
    def test_a_malformed_key_is_none(self, key: str) -> None:
        assert login_browser(_trusted(**{BROWSER_KEY_HEADER: key})).key is None

    def test_nothing_sent_is_an_unknown_new_browser(self) -> None:
        browser = login_browser(_trusted())

        assert browser.key is None
        assert browser.name == UNKNOWN_DEVICE_NAME
