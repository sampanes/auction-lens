"""Reading the canonical listing files this project analyses.

JSON and CSV are the boundary between acquiring data and analyzing it. Anything
that can produce these two shapes -- an export, a scraper, a hand-written file --
can feed the rest of the project without touching it.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..models import Listing

LISTINGS_KEY = "listings"

# Unlike price and closing time, these facts do not change between two pages
# that show the same physical item. A later category sweep or product page may
# know one that the first search did not.
STABLE_IDENTITY_FIELDS = ("brand", "model", "category")


def unique_lots(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep each lot once, however many pages happened to list it.

    A search page and a category sweep both describe whole pages of lots, so one
    lot routinely appears in two of them. The first sighting keeps every
    auction-state value, while a later one may fill a missing stable identity
    fact. Identity is the physical item where the provider names it, so that a
    lot relisted after failing to sell is still recognised as itself.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("source") or ""),
            str(row.get("inventory_id") or row["listing_id"]),
        )
        if key not in seen:
            seen[key] = dict(row)
            continue
        first = seen[key]
        for field in STABLE_IDENTITY_FIELDS:
            if not first.get(field) and row.get(field):
                first[field] = row[field]
    return list(seen.values())


# Excel, Notepad, and PowerShell all write a byte-order mark ahead of the first
# character. Reading as utf-8-sig accepts a file with or without one.
INPUT_ENCODING = "utf-8-sig"


def load_listings(path: str | Path) -> list[Listing]:
    """Read a .json or .csv file into validated listings."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        rows = _read_json_rows(source)
    elif suffix == ".csv":
        rows = _read_csv_rows(source)
    else:
        raise ValueError("input must be a .json or .csv file")
    return _build_listings(rows, source)


def _read_json_rows(source: Path) -> list[Any]:
    """Accept either a bare list of listings or an object wrapping one."""
    with source.open("r", encoding=INPUT_ENCODING) as handle:
        payload = json.load(handle)
    rows = payload.get(LISTINGS_KEY) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("JSON input must be a list or contain a 'listings' list")
    return rows


def _read_csv_rows(source: Path) -> list[dict[str, Any]]:
    with source.open("r", encoding=INPUT_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def _build_listings(rows: list[Any], source: Path) -> list[Listing]:
    """Validate rows with enough context for a person to repair the input."""
    listings = []
    first_row_by_key: dict[tuple[str, str], int] = {}
    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"{source}: listing {row_number} must be an object")
        try:
            listing = Listing.from_mapping(row)
        except ValueError as error:
            raise ValueError(f"{source}: listing {row_number}: {error}") from error

        key = (listing.source, listing.listing_id)
        if key in first_row_by_key:
            first_row = first_row_by_key[key]
            raise ValueError(
                f"{source}: listing {row_number} duplicates "
                f"{listing.source}/{listing.listing_id} from listing {first_row}"
            )
        first_row_by_key[key] = row_number
        listings.append(listing)
    return listings
