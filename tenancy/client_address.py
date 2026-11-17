"""The address a request really came from, as the Next.js server reports it (ADR 0004).

This is the only code in Django that reads a client-address header (a test checks it). The value
is used only to compare with Office Networks; it is never stored, logged or put in an error.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address

from django.http import HttpRequest

from tenancy.middleware import has_internal_token

CLIENT_ADDRESS_HEADER = "X-Client-IP"
# The longest text form of an IPv6 address (with an IPv4 tail); anything longer is not one.
MAX_ADDRESS_LENGTH = 45

type ClientAddress = IPv4Address | IPv6Address


def client_address(request: HttpRequest) -> ClientAddress | None:
    """Return the client's address from `X-Client-IP`, or None.

    Trusted only on a call that carries the Next.js server's internal token, which sets the header
    from Cloudflare's `CF-Connecting-IP` and drops any the browser sent. A missing or malformed
    value is None, which counts as "not on an Office Network"; it never raises. An IPv4 address
    written as IPv6 (`::ffff:a.b.c.d`) is given as IPv4.
    """
    if not has_internal_token(request):
        return None
    value = request.headers.get(CLIENT_ADDRESS_HEADER, "")
    if not 0 < len(value) <= MAX_ADDRESS_LENGTH or "%" in value or value != value.strip():
        return None
    try:
        address = ip_address(value)
    except ValueError:
        # silent: not an address means no address
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address
