"""The lots a person is following, in one ignored JSON file.

SQLite remembers every listing this project has ever scored. This file is the
much shorter list a person actually cares about, kept in a shape they can open,
read, and edit by hand: what they think a lot is worth, how badly they want it,
and every price it has stood at since they started watching.

That is why it is JSON and not another SQLite table. The fields a person fills
in -- estimate, stars, state, note -- are never overwritten by a run. A run only
ever appends one reading per lot it saw, so scanning hourly leaves an hourly
trail and scanning once leaves a single point.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..fields import (
    parse_money,
    parse_optional_money,
    parse_utc_datetime,
    parse_whole_number,
)
from ..file_io import read_json, write_json_atomically
from ..models import (
    FEWEST_STARS,
    Listing,
    PriceReading,
    WatchedItem,
    WatchState,
    uid_of,
)

DEFAULT_WATCHLIST_FILE = "private/watchlist.json"

# Bumped only when an old file can no longer be read as it stands.
FILE_VERSION = 1

ITEMS_KEY = "items"


@dataclass(frozen=True)
class WatchlistStore:
    """One JSON file of followed lots, read and written whole."""

    path: Path

    def items(self) -> tuple[WatchedItem, ...]:
        """Every followed lot, in the order the file lists them."""
        document = read_json(self.path, default={})
        rows = document.get(ITEMS_KEY, []) if isinstance(document, dict) else []
        return tuple(self._read(row) for row in rows)

    def get(self, source: str, listing_id: str) -> WatchedItem | None:
        """One followed lot, or nothing when it is not being followed yet."""
        wanted = uid_of(source, listing_id)
        return next((item for item in self.items() if item.uid == wanted), None)

    def save(self, item: WatchedItem) -> None:
        """Add a lot, or replace the one already stored under its uid."""
        kept = [stored for stored in self.items() if stored.uid != item.uid]
        self._write([*kept, item])

    def drop(self, source: str, listing_id: str) -> bool:
        """Stop following a lot; say whether there was one to stop following."""
        unwanted = uid_of(source, listing_id)
        stored = self.items()
        kept = [item for item in stored if item.uid != unwanted]
        if len(kept) == len(stored):
            return False
        self._write(kept)
        return True

    def record(self, seen: Iterable[tuple[Listing, Decimal]]) -> int:
        """Append one reading per lot seen, and say how many lots were touched.

        The whole run is written once. A lot seen twice at the same instant --
        the same input file read twice, say -- leaves one reading, not two.
        """
        stored = {item.uid: item for item in self.items()}
        touched = 0
        for listing, total_cost in seen:
            item = stored.get(uid_of(listing.source, listing.listing_id))
            updated = _observed(item, listing, total_cost)
            if updated is not None:
                stored[updated.uid] = updated
                touched += 1
        if touched:
            self._write(list(stored.values()))
        return touched

    def _write(self, items: list[WatchedItem]) -> None:
        document = {"version": FILE_VERSION, ITEMS_KEY: [_as_json(item) for item in items]}
        write_json_atomically(self.path, document)

    def _read(self, row: Any) -> WatchedItem:
        """Build one item, naming the entry when a hand edit made it unreadable."""
        if not isinstance(row, dict):
            raise ValueError(f"{self.path}: every watchlist item must be an object")
        try:
            return _from_json(row)
        except (ValueError, KeyError) as error:
            name = row.get("uid") or row.get("listing_id") or "an item"
            raise ValueError(f"{self.path}: {name}: {error}") from error


def _observed(
    item: WatchedItem | None, listing: Listing, total_cost: Decimal
) -> WatchedItem | None:
    """Refresh what the provider said, and append this look at the price.

    Returns nothing when this exact instant is already recorded, so re-running
    over the same input file does not double every trail.
    """
    reading = PriceReading(
        scanned_at=listing.observed_at,
        current_bid=listing.current_bid,
        total_cost=total_cost,
        bid_count=listing.bid_count,
    )
    readings = () if item is None else item.readings
    if any(stored.scanned_at == reading.scanned_at for stored in readings):
        return None
    return WatchedItem(
        source=listing.source,
        listing_id=listing.listing_id,
        title=listing.title,
        url=listing.url,
        image_url=listing.image_url,
        estimated_retail=listing.estimated_retail,
        my_estimate=None if item is None else item.my_estimate,
        state=WatchState.WATCHING if item is None else item.state,
        stars=FEWEST_STARS if item is None else item.stars,
        note="" if item is None else item.note,
        readings=(*readings, reading),
    )


def _as_json(item: WatchedItem) -> dict[str, Any]:
    """Write money as text, so a rounded float can never become the record.

    ``uid`` and ``tag`` are written for a person reading or searching the file.
    Both are derived -- from source and listing id, and from state -- so editing
    either of them in place changes nothing.
    """
    return {
        "uid": item.uid,
        "source": item.source,
        "listing_id": item.listing_id,
        "title": item.title,
        "url": item.url,
        "image_url": item.image_url,
        "estimated_retail": _money(item.estimated_retail),
        "my_estimate": _money(item.my_estimate),
        "state": str(item.state),
        "tag": str(item.tag),
        "stars": item.stars,
        "note": item.note,
        "readings": [_reading_as_json(reading) for reading in item.readings],
    }


def _reading_as_json(reading: PriceReading) -> dict[str, Any]:
    return {
        "scanned_at": reading.scanned_at.isoformat(),
        "current_bid": str(reading.current_bid),
        "total_cost": str(reading.total_cost),
        "bid_count": reading.bid_count,
    }


def _from_json(row: dict[str, Any]) -> WatchedItem:
    """Hold a hand-edited file to the same standard as any other input."""
    return WatchedItem(
        source=str(row["source"]),
        listing_id=str(row["listing_id"]),
        title=str(row.get("title", "")),
        url=str(row.get("url", "")),
        image_url=str(row.get("image_url", "")),
        estimated_retail=parse_optional_money(
            row.get("estimated_retail"), field_name="estimated_retail"
        ),
        my_estimate=parse_optional_money(row.get("my_estimate"), field_name="my_estimate"),
        state=row.get("state", WatchState.WATCHING),
        stars=parse_whole_number(row.get("stars"), field_name="stars"),
        note=str(row.get("note", "")),
        readings=tuple(_reading_from_json(entry) for entry in row.get("readings", [])),
    )


def _reading_from_json(entry: dict[str, Any]) -> PriceReading:
    scanned_at = parse_utc_datetime(entry.get("scanned_at"), field_name="scanned_at")
    if scanned_at is None:
        raise ValueError("scanned_at is required on every reading")
    return PriceReading(
        scanned_at=scanned_at,
        current_bid=parse_money(entry.get("current_bid"), field_name="current_bid"),
        total_cost=parse_money(entry.get("total_cost"), field_name="total_cost"),
        bid_count=parse_whole_number(entry.get("bid_count"), field_name="bid_count"),
    )


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
