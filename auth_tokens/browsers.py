"""Which browser a login comes from, as the Next.js server reports it (issue 71).

The Next.js server keeps a random value for each browser in its own HTTP-only cookie and sends it
as `X-Browser-Key`, with the browser's own description as `X-Browser-Agent`. Both are trusted only
on a call that carries the internal token. They name and group a person's Signed-in devices and
are never used for security. No address is read here (ADR 0004).
"""

from __future__ import annotations

import re

from django.http import HttpRequest

from auth_tokens.models import UNKNOWN_DEVICE_NAME
from auth_tokens.types import Browser
from tenancy.middleware import has_internal_token

BROWSER_KEY_HEADER = "X-Browser-Key"
BROWSER_AGENT_HEADER = "X-Browser-Agent"
# Only the start of a description is read; real ones are far shorter.
MAX_AGENT_LENGTH = 512
UNKNOWN_BROWSER = "Unknown browser"
UNKNOWN_SYSTEM = "Unknown system"

_KEY = re.compile(r"[0-9a-f]{32}")

# First match wins: browsers built on Chrome name Chrome too, and Chrome names Safari.
_BROWSERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Edge", re.compile(r"\bEdg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"\b(?:OPR|OPiOS|Opera)/")),
    ("Samsung Internet", re.compile(r"\bSamsungBrowser/")),
    ("Firefox", re.compile(r"\b(?:Firefox|FxiOS)/")),
    ("Chrome", re.compile(r"\b(?:Chrome|CriOS)/")),
    ("Safari", re.compile(r"\bVersion/[\d.]+.*\bSafari/")),
)
# Android and ChromeOS say Linux, and iPhones say "like Mac OS X", so they come first.
_SYSTEMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Windows", re.compile(r"\bWindows\b")),
    ("Android", re.compile(r"\bAndroid\b")),
    ("iOS", re.compile(r"\b(?:iPhone|iPad|iPod)\b")),
    ("ChromeOS", re.compile(r"\bCrOS\b")),
    ("macOS", re.compile(r"\b(?:Macintosh|Mac OS X)\b")),
    ("Linux", re.compile(r"\bLinux\b")),
)


def device_name(agent: str) -> str:
    """Return "browser on system" ("Chrome on Windows") from a browser's description.

    The name is chosen from fixed words only, so nothing the browser sent ever reaches it.
    """
    text = agent[:MAX_AGENT_LENGTH]
    browser = _first_match(_BROWSERS, text) or UNKNOWN_BROWSER
    system = _first_match(_SYSTEMS, text) or UNKNOWN_SYSTEM
    return f"{browser} on {system}"


def login_browser(request: HttpRequest) -> Browser:
    """Return the browser a login request comes from; unknown unless the Next.js server says."""
    if not has_internal_token(request):
        return Browser(key=None, name=UNKNOWN_DEVICE_NAME)
    key = request.headers.get(BROWSER_KEY_HEADER, "")
    return Browser(
        key=key if _KEY.fullmatch(key) else None,
        name=device_name(request.headers.get(BROWSER_AGENT_HEADER, "")),
    )


def _first_match(candidates: tuple[tuple[str, re.Pattern[str]], ...], text: str) -> str | None:
    return next((name for name, pattern in candidates if pattern.search(text)), None)
