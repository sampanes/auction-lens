"""Turning a listing into the query a valuation source expects.

Both the research-link and JSON-API adapters build a URL from the same four
placeholders, so the substitution rules live in one place and cannot drift.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from ..models import Listing

PLACEHOLDERS = ("query", "brand", "model", "category")


def research_query(listing: Listing) -> str:
    """Use precise structured identity, or the whole title when it is incomplete."""
    if listing.brand and listing.model:
        return f"{listing.brand} {listing.model}"
    return listing.title


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
