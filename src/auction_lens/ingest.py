from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Listing


def load_listings(path: str | Path) -> list[Listing]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = payload["listings"] if isinstance(payload, dict) else payload
    elif suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    else:
        raise ValueError("input must be a .json or .csv file")
    if not isinstance(rows, list):
        raise ValueError("JSON input must be a list or contain a 'listings' list")
    return [Listing.from_mapping(row) for row in rows]
