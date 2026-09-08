"""Fail-closed HTTP rules shared by every configurable network adapter."""

from __future__ import annotations

import re
from collections.abc import Callable
from ipaddress import ip_address
from urllib.parse import SplitResult, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

LEGACY_IPV4_ADDRESS = re.compile(
    r"^(?:0[xX][0-9A-Fa-f]+|[0-9]+)"
    r"(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+)){0,3}$"
)


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
            or LEGACY_IPV4_ADDRESS.fullmatch(host)
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
