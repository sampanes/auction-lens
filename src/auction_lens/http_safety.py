"""Shared rules that keep configurable HTTP requests on public HTTPS origins.

Configured URLs are checked for operator mistakes. Redirects are pinned to the
authorized origin because the remote server, not the operator, chooses them and
requests can carry identifying or secret headers.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from ipaddress import ip_address
from urllib.parse import SplitResult, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

# ``ip_address`` deliberately accepts only modern notation, while DNS still
# understands shorthand such as 127.1 and 0x7f.1. Refusing numeric-looking host
# names keeps those alternate spellings from evading the public-address check.
NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){0,3}$", re.I)


def require_public_https(value: str) -> None:
    """Accept only credential-free HTTPS URLs aimed at an unambiguous public host."""
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("authorized source URL must be public HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("credentials are not allowed in the source URL")
    host = _normalized_host(parsed.hostname)
    try:
        address = ip_address(host)
    except ValueError:
        if (
            host == "localhost"
            or host.endswith(".localhost")
            or "." not in host
            or NUMERIC_HOST.fullmatch(host)
        ):
            raise ValueError(
                "authorized source URL must use a public, fully qualified host"
            ) from None
    else:
        if not address.is_global or any(
            (
                address.is_private,
                address.is_loopback,
                address.is_link_local,
                address.is_reserved,
                address.is_unspecified,
                address.is_multicast,
            )
        ):
            raise ValueError("authorized source URL must not use a non-public IP address")


class PublicHttpsRedirectHandler(HTTPRedirectHandler):
    """Allow redirects only within the exact public HTTPS origin first requested."""

    def redirect_request(
        self, request, response, code, message, headers, new_url
    ):
        require_public_https(new_url)
        original = urlsplit(request.full_url)
        redirected = urlsplit(new_url)
        if _origin(redirected) != _origin(original):
            raise RuntimeError(
                "source redirected to a different origin; configure that final URL "
                "and reconfirm authorization before contacting it"
            )
        return super().redirect_request(
            request, response, code, message, headers, new_url
        )


def public_https_opener(*handlers) -> Callable:
    """Return an opener whose redirects cannot escape the authorized HTTPS origin."""
    return build_opener(PublicHttpsRedirectHandler(), *handlers).open


def _origin(parsed: SplitResult) -> tuple[str, str, int]:
    """Normalize the parts that define which server receives request headers."""
    return parsed.scheme.lower(), _normalized_host(parsed.hostname or ""), parsed.port or 443


def _normalized_host(host: str) -> str:
    return host.rstrip(".").lower()
