"""Turning one saved Nellis product page into a canonical listing row."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

NELLIS_SOURCE = "nellis"
PRODUCT_ROUTE = "routes/p.$title.$productId._index"
REMIX_CONTEXT_MARKER = "window.__remixContext"

GRADE_AXIS_NAMES = {
    "conditionType": "condition",
    "functionalType": "functional",
    "damageType": "damage",
    "missingPartsType": "missing_parts",
    "assemblyType": "assembly",
    "packageType": "package",
}

_STREAM_CHUNK = re.compile(
    r"window\.__remixContext\.streamController\.enqueue\(\s*(\"(?:\\.|[^\"\\])*\")",
    re.DOTALL,
)


def load_nellis_product_page(path: str | Path, *, page_url: str) -> dict[str, Any]:
    """Read a saved product page and return the canonical row inside it."""
    source_path = Path(path)
    html = source_path.read_text(encoding="utf-8-sig")
    try:
        product = _find_product(_remix_payloads(html))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{source_path}: cannot read Nellis product data: {error}") from error
    if product is None:
        raise ValueError(f"{source_path}: Nellis product data was not found")
    return canonicalize_nellis_product(product, page_url=page_url)


def canonicalize_nellis_product(product: dict[str, Any], *, page_url: str) -> dict[str, Any]:
    """Rename one Nellis product object to the provider-independent file shape."""
    grade = product.get("grade")
    canonical_grade = _canonical_grade(grade) if isinstance(grade, dict) else None
    row = {
        "source": NELLIS_SOURCE,
        "listing_id": product.get("inventoryNumber") or product.get("id"),
        "title": product.get("title"),
        "url": page_url,
        "current_bid": product.get("currentPrice"),
        "estimated_retail": product.get("retailPrice"),
        "bid_count": product.get("bidCount", 0),
        "ends_at": product.get("closeTime"),
        "location": _location_name(product.get("location")),
        "photo_urls": _photo_urls(product.get("photos")),
        "grade": canonical_grade,
        "quality_rating": grade.get("rating") if isinstance(grade, dict) else None,
        "category": product.get("taxonomyLevel2") or product.get("taxonomyLevel1"),
    }
    return {name: value for name, value in row.items() if value not in (None, "", [])}


def write_canonical_listings(rows: list[dict[str, Any]], path: str | Path) -> None:
    """Write rows in the wrapped JSON format accepted by ``auction-lens run``."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"listings": rows}, indent=2) + "\n",
        encoding="utf-8",
    )


def _remix_payloads(html: str) -> list[Any]:
    """Decode both initial Remix state and streamed loader-data chunks."""
    payloads = []
    assignment = re.search(rf"{re.escape(REMIX_CONTEXT_MARKER)}\s*=\s*", html)
    if assignment:
        payload, _ = json.JSONDecoder().raw_decode(html, assignment.end())
        payloads.append(payload)
    for match in _STREAM_CHUNK.finditer(html):
        chunk = json.loads(match.group(1))
        payloads.append(json.loads(chunk))
    return payloads


def _find_product(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        route_data = value.get(PRODUCT_ROUTE)
        if isinstance(route_data, dict) and isinstance(route_data.get("product"), dict):
            return route_data["product"]
        for child in value.values():
            product = _find_product(child)
            if product is not None:
                return product
    elif isinstance(value, list):
        for child in value:
            product = _find_product(child)
            if product is not None:
                return product
    return None


def _canonical_grade(grade: dict[str, Any]) -> dict[str, str]:
    return {
        canonical_name: answer["description"]
        for provider_name, canonical_name in GRADE_AXIS_NAMES.items()
        if isinstance((answer := grade.get(provider_name)), dict)
        and isinstance(answer.get("description"), str)
    }


def _photo_urls(photos: Any) -> list[str]:
    if not isinstance(photos, list):
        return []
    return [photo["url"] for photo in photos if isinstance(photo, dict) and photo.get("url")]


def _location_name(location: Any) -> str | None:
    if isinstance(location, str):
        return location
    if isinstance(location, dict):
        return location.get("name") or location.get("title")
    return None
