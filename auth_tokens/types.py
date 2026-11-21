from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from auth_tokens.models import TokenKind
from platform_admin.models import PlatformAdmin
from staff.models import Staff

# Who can hold login tokens: Staff on their Consultancy, Platform Admins on the Platform.
type Person = Staff | PlatformAdmin


@dataclass(frozen=True)
class TokenPair:
    """A short access token and the refresh token that renews it."""

    access: str
    refresh: str


@dataclass(frozen=True)
class TokenClaims:
    """What a verified token says about where it belongs and whom it is for."""

    tenant_schema: str
    kind: TokenKind
    # Whom the token is for, by UUID (ADR 0027): a Staff member or a Platform Admin.
    user: UUID
    auth_version: int
    jti: str
    device: str
    # The Support Access visit a `support_access` token belongs to; None for every other kind.
    visit: UUID | None = None
    # Their database numbers, once the person is found (0 and None until then).
    user_id: int = 0
    visit_id: int | None = None


@dataclass(frozen=True)
class Browser:
    """The browser a login comes from (issue 71), made by auth_tokens.browsers.login_browser.

    `key` is its random value, if the Next.js server sent one; `name` is "Chrome on Windows".
    """

    key: str | None
    name: str


@dataclass(frozen=True)
class Device:
    """One browser or phone a person logged in on, kept the same by every refresh."""

    id: str
    signed_in_at: datetime
    # "Chrome on Windows"; empty keeps the name this device already has.
    name: str = ""


@dataclass(frozen=True)
class SignedInDevice:
    """A row of Signed-in devices: when it logged in, last renewed its tokens and logged out.

    `logged_out_at` is None while the device is still logged in.
    """

    name: str
    signed_in_at: datetime
    last_used_at: datetime
    logged_out_at: datetime | None
    is_current: bool
