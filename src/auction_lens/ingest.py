"""Reading canonical listing files.

JSON and CSV are the boundary between acquiring data and analyzing it. Anything
that can produce these two shapes -- an export, a scraper, a hand-written file --
can feed the rest of the project without touching it.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .models import Listing

LISTINGS_KEY = "listings"

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
    return [Listing.from_mapping(row) for row in rows]


def _read_json_rows(source: Path) -> list[dict[str, Any]]:
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
