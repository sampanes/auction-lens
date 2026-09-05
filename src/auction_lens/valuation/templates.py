"""Turning a listing into the query a valuation source expects.

Both the research-link and JSON-API adapters build a URL from the same four
placeholders, so the substitution rules live in one place and cannot drift.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from ..models import Listing

PLACEHOLDERS = ("query", "brand", "model", "category")


def research_query(listing: Listing) -> str:
    """Prefer brand and model; fall back to the title when they are missing."""
    identifying = [value for value in (listing.brand, listing.model) if value]
    return " ".join(identifying) or listing.title


def fill_template(template: str, listing: Listing) -> str:
    """Replace {query}, {brand}, {model}, and {category} with encoded values."""
    values = {
        "query": research_query(listing),
        "brand": listing.brand,
        "model": listing.model,
        "category": listing.category,
    }
    filled = template
    for name in PLACEHOLDERS:
        filled = filled.replace("{" + name + "}", quote_plus(values[name]))
    return filled
