"""Fail-closed HTTP rules shared by every configurable network adapter.

The risk worth defending against is a redirect. A configured URL is written by
the operator, but where it redirects to is decided by the remote server, and
every request carries a user agent with the operator's real contact address. So
a redirect may not leave the origin that was authorized.

Deliberately not defended against: an attacker who can edit the configuration
file. Once someone has that access, no check on the URL they wrote can help, so
a rule guarding against it costs a reader something and protects nobody.

The checks on a configured URL therefore exist to catch a mistake and say so
clearly, not to stop an adversary. "A public host name has letters in it" is
the whole rule, and it rejects a shorthand address like 127.1 that the standard
library declines to parse as an address at all. An earlier version matched
legacy octal and hexadecimal spellings with a regular expression, which caught
the same mistakes while asking the reader to recognise several notations to see
that it did.
"""

from __future__ import annotations

from collections.abc import Callable
from ipaddress import ip_address
from urllib.parse import SplitResult, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


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
            or not any(character.isalpha() for character in host)
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
