"""Turning one saved Nellis product page into a canonical listing row.

The page is a server-rendered shell whose data arrives in a streamed payload at
the bottom of the HTML. That payload is the authoritative copy: it is typed,
complete, and identical to what the site's own client reads. The rendered
markup carries the same facts, but only as text inside styled elements, so
reading it would mean depending on class names that are nobody's contract.

This module is the only place that knows the provider's field names. Everything
downstream sees the canonical row described in docs/DATA_ACQUISITION.md, which
is the same shape a hand-written JSON file uses.

The provider gives two ids. ``id`` names this auction and is what the page URL
is built from; ``inventoryNumber`` names the physical item, and survives the
lot being relisted after it fails to sell. Both are carried, because they answer
different questions.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .turbo_stream import decode

# Where the streamed payload sits, and where the page states its own address.
PAYLOAD_PATTERN = re.compile(r'streamController\.enqueue\("(.*?)"\);', re.DOTALL)
CANONICAL_URL_PATTERN = re.compile(
    r'<meta\s+property="og:url"\s+content="([^"]+)"', re.IGNORECASE
)

# The route whose loader carries the product. A page that does not have it is
# not a product page, which is worth saying plainly.
PRODUCT_ROUTE = "routes/p.$title.$productId._index"

# The provider names each graded axis after its own database table. Renaming
# them is this module's job; grading.py knows only the canonical names.
AXIS_NAMES = {
    "conditionType": "condition",
    "functionalType": "functional",
    "damageType": "damage",
    "missingPartsType": "missing_parts",
    "assemblyType": "assembly",
    "packageType": "package",
}

RATING_KEY = "rating"

# A photo has to be openable to be worth recording.
WEB_ADDRESS_PREFIXES = ("https://", "http://")


def read_product_page(html: str, *, source: str) -> dict[str, Any]:
    """Read one saved product page into a canonical listing row."""
    product = _product(html)
    grade = product.get("grade") or {}
    return {
        "source": source,
        "listing_id": str(product["id"]),
        "inventory_id": str(product.get("inventoryNumber") or "").strip(),
        "title": str(product.get("title", "")).strip(),
        "url": _canonical_url(html),
        "current_bid": _amount(product.get("currentPrice")),
        "estimated_retail": _amount(product.get("retailPrice")),
        "bid_count": product.get("bidCount", 0),
        "ends_at": product.get("closeTime"),
        "location": _location(product),
        "category": _category(product),
        "photo_urls": _photos(product),
        "grade": canonical_grade(grade),
        "quality_rating": grade.get(RATING_KEY),
    }


def _product(html: str) -> dict[str, Any]:
    """Find the streamed payload and take the product out of it."""
    match = PAYLOAD_PATTERN.search(html)
    if match is None:
        raise ValueError("no streamed payload found; this is not a product page")
    payload = _unescape(match.group(1))
    routes = decode(payload).get("loaderData") or {}
    route = routes.get(PRODUCT_ROUTE) or {}
    product = route.get("product")
    if not isinstance(product, dict) or "id" not in product:
        raise ValueError("streamed payload carried no product")
    return product


def _unescape(literal: str) -> str:
    """A JavaScript string literal is escaped the way a JSON string is."""
    try:
        return json.loads(f'"{literal}"')
    except ValueError as error:
        raise ValueError(f"streamed payload is not readable: {error}") from error


def _canonical_url(html: str) -> str:
    """Take the page's own address rather than reconstructing it from a slug."""
    match = CANONICAL_URL_PATTERN.search(html)
    if match is None:
        raise ValueError("page does not state its own canonical URL")
    return match.group(1)


def canonical_grade(grade: dict[str, Any]) -> dict[str, str]:
    """Rename the provider's axes, keeping only the ones it actually answered.

    Public because it is the one authority on the rename: anything reading a
    recorded provider payload has to arrive at the same canonical names.
    """
    answers = {}
    for provider_name, canonical_name in AXIS_NAMES.items():
        answer = grade.get(provider_name)
        if isinstance(answer, dict) and answer.get("description"):
            answers[canonical_name] = str(answer["description"])
    return answers


def _photos(product: dict[str, Any]) -> list[str]:
    """Keep the gallery in the order given; the last photo is of this lot.

    ``url`` is the address that actually fetches the image. ``fullPath`` is the
    provider's own storage path, which is relative for photographs it took
    itself, so reading that one puts an unopenable string in a report. Anything
    that is not an absolute web address is left out for the same reason.
    """
    urls = []
    for photo in product.get("photos") or []:
        url = str(photo.get("url") or "").strip()
        if url.startswith(WEB_ADDRESS_PREFIXES):
            urls.append(url)
    return urls


def _location(product: dict[str, Any]) -> str:
    location = product.get("location") or {}
    return str(location.get("name", "")).strip()


def _category(product: dict[str, Any]) -> str:
    """Prefer the narrower taxonomy, which is what interest rules match on."""
    narrow = str(product.get("taxonomyLevel2") or "").strip()
    return narrow or str(product.get("taxonomyLevel1") or "").strip()


def _amount(value: Any) -> str:
    """Money as text, so a float's rounding never becomes the record."""
    return "0" if value is None else str(value)
