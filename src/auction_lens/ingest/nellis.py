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
from urllib.parse import unquote_plus, urljoin

from .turbo_stream import decode

# Where the streamed payload sits, and where the page states its own address.
PAYLOAD_PATTERN = re.compile(r'streamController\.enqueue\("(.*?)"\);', re.DOTALL)
CANONICAL_URL_PATTERN = re.compile(
    r'<meta\s+property="og:url"\s+content="([^"]+)"', re.IGNORECASE
)

# The route whose loader carries the product. A page that does not have it is
# not a product page, which is worth saying plainly.
PRODUCT_ROUTE = "routes/p.$title.$productId._index"

# A search page carries every result in one payload, complete with grades, so
# one request describes a whole page of lots rather than one lot.
SEARCH_ROUTE = "routes/search"
PRODUCTS_KEY = "products"

# Search hits omit taxonomy and brand, but the surrounding route keeps the
# filter that produced the page and the provider's brand vocabulary. These are
# evidence only when they also agree with the page itself; a search term alone
# is never treated as a category or brand.
CATEGORY_FILTER = "Taxonomy Level 1"
TAXONOMY_FACET = "taxonomy1"
BRAND_FACET = "brand"
WORD_PATTERN = re.compile(r"[^\W_]+")

# Each result is linked from the rendered markup, and the link ends in the
# auction id, which is how a product is matched to its own address.
PRODUCT_LINK_PATTERN = re.compile(r'href="(/p/[^"]*?/(\d+))"')

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
    return _row(_product(html), source=source, url=_canonical_url(html))


def _row(
    product: dict[str, Any],
    *,
    source: str,
    url: str,
    category: str = "",
    brand: str = "",
) -> dict[str, Any]:
    """The one mapping from a provider product to a canonical row.

    A product object is the same shape whether it came from its own page or from
    a page of search results, so both readers arrive here.
    """
    grade = product.get("grade") or {}
    row = {
        "source": source,
        "listing_id": str(product["id"]),
        "inventory_id": str(product.get("inventoryNumber") or "").strip(),
        "title": str(product.get("title", "")).strip(),
        "url": url,
        "current_bid": _amount(product.get("currentPrice")),
        "estimated_retail": _amount(product.get("retailPrice")),
        "bid_count": product.get("bidCount", 0),
        "ends_at": product.get("closeTime"),
        "location": _location(product),
        "notes": _notes(product),
        "photo_urls": _photos(product),
        "grade": canonical_grade(grade),
        "quality_rating": grade.get(RATING_KEY),
    }
    category = _category(product) or category
    if category:
        row["category"] = category
    direct_brand = str(product.get("brand") or "").strip()
    brand = direct_brand or brand
    if brand:
        row["brand"] = brand
    return row


def read_search_page(html: str, *, source: str, page_url: str) -> list[dict[str, Any]]:
    """Read every open lot a saved search page lists, as canonical rows.

    This is the polite way to discover lots: one request describes a whole page
    of them, where asking for each product page separately would be forty.

    A result object carries no taxonomy or brand. The surrounding route does
    retain an applied category filter and its brand facet, so those can supply
    conservative context without asking for every lot's own page. Lots the page
    marks as closed are left out, because nothing can be bid on any more.
    """
    payload = _payload(html)
    route = (payload.get("loaderData") or {}).get(SEARCH_ROUTE) or {}
    listed = route.get(PRODUCTS_KEY)
    if not isinstance(listed, list):
        raise ValueError("no search results found; this is not a search page")
    addresses = _product_addresses(html, page_url)
    category = _selected_category(route)
    return [
        _row(
            product,
            source=source,
            url=addresses.get(str(product.get("id")), ""),
            category=category,
            brand=_title_brand(product, route),
        )
        for product in listed
        if isinstance(product, dict) and product.get("id") and not product.get("isClosed")
    ]


def read_saved_page(html: str, *, source: str, page_url: str = "") -> list[dict[str, Any]]:
    """Read one saved page, whichever kind it is, into canonical rows.

    Discovery saves search pages and fetching saves product pages, and both land
    in the same cache. Which one a file holds is stated in the payload itself,
    so a reader can tell rather than having to be told. Returning a list either
    way means a caller never has to know which it got.
    """
    routes = _payload(html).get("loaderData") or {}
    if SEARCH_ROUTE in routes:
        return read_search_page(html, source=source, page_url=page_url)
    return [read_product_page(html, source=source)]


def _product_addresses(html: str, page_url: str) -> dict[str, str]:
    """Match each result to the address the page itself links it at."""
    return {
        match.group(2): urljoin(page_url, match.group(1))
        for match in PRODUCT_LINK_PATTERN.finditer(html)
    }


def _payload(html: str) -> dict[str, Any]:
    """Find the streamed payload at the foot of the page and decode it."""
    match = PAYLOAD_PATTERN.search(html)
    if match is None:
        raise ValueError("no streamed payload found; this is not a provider page")
    decoded = decode(_unescape(match.group(1)))
    if not isinstance(decoded, dict):
        raise ValueError("streamed payload was not an object")
    return decoded


def _product(html: str) -> dict[str, Any]:
    """Take the single product a lot's own page describes."""
    routes = _payload(html).get("loaderData") or {}
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


def _notes(product: dict[str, Any]) -> str:
    """What the warehouse wrote about this particular item, on one line.

    Notes arrive with newlines in them because a person typed them into a box
    over several visits. Flattened here so that matching, which reads one
    string, cannot be defeated by where somebody pressed Enter.
    """
    return " ".join(str(product.get("notes") or "").split())


def _location(product: dict[str, Any]) -> str:
    location = product.get("location") or {}
    return str(location.get("name", "")).strip()


def _category(product: dict[str, Any]) -> str:
    """Prefer the narrower taxonomy, which is what interest rules match on."""
    narrow = str(product.get("taxonomyLevel2") or "").strip()
    return narrow or str(product.get("taxonomyLevel1") or "").strip()


def _selected_category(route: dict[str, Any]) -> str:
    """Use one applied top-level category only when its facet confirms it."""
    selected = set()
    for item in route.get("selectedFilters") or []:
        if not isinstance(item, str):
            continue
        name, separator, value = _filter_parts(item)
        if separator and name == CATEGORY_FILTER and value:
            selected.add(value)
    if len(selected) != 1:
        return ""

    category = next(iter(selected))
    facets = route.get("facets") or {}
    taxonomy = facets.get(TAXONOMY_FACET) if isinstance(facets, dict) else None
    if not isinstance(taxonomy, dict):
        return ""
    confirmed = [
        str(name).strip()
        for name in taxonomy
        if str(name).strip().casefold() == category.casefold()
    ]
    return confirmed[0] if len(confirmed) == 1 else ""


def _filter_parts(value: str) -> tuple[str, str, str]:
    """Decode the provider's ``Name:value`` selected-filter notation."""
    name, separator, selected = unquote_plus(value).partition(":")
    return name.strip(), separator, selected.strip()


def _title_brand(product: dict[str, Any], route: dict[str, Any]) -> str:
    """Take the longest provider brand facet that begins the product title.

    Matching later in a title would mistake compatibility text for the product's
    own brand. A partial word is not evidence either: ``GE`` must not match
    ``large``. When equally specific facets disagree, saying nothing is safer
    than choosing one arbitrarily.
    """
    title_words = _words(product.get("title"))
    facets = route.get("facets") or {}
    brands = facets.get(BRAND_FACET) if isinstance(facets, dict) else None
    if not title_words or not isinstance(brands, dict):
        return ""

    matches: list[tuple[tuple[str, ...], str]] = []
    for candidate in brands:
        brand = str(candidate).strip()
        brand_words = _words(brand)
        if brand_words and title_words[: len(brand_words)] == brand_words:
            matches.append((brand_words, brand))
    if not matches:
        return ""

    longest = max(len(words) for words, _ in matches)
    best = [brand for words, brand in matches if len(words) == longest]
    return best[0] if len(best) == 1 else ""


def _words(value: Any) -> tuple[str, ...]:
    """Comparable words, treating ``&`` and ``and`` as the same brand text."""
    text = str(value or "").casefold().replace("&", " and ")
    return tuple(WORD_PATTERN.findall(text))


def _amount(value: Any) -> str:
    """Money as text, so a float's rounding never becomes the record."""
    return "0" if value is None else str(value)
