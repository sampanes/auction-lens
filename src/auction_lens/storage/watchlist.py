"""The lots a person is following, in one ignored JSON file.

SQLite remembers every listing this project has ever scored. This file is the
much shorter list a person actually cares about, kept in a shape they can open,
read, and edit by hand: what they think a lot is worth, how badly they want it,
and every price it has stood at since they started watching.

That is why it is JSON and not another SQLite table. The fields a person fills
in -- estimate, verdict, note, fulfillment review -- are never overwritten by a
run. A run refreshes provider facts and matched-interest provenance, then appends
at most one reading per lot it saw.
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
from ..grading import ConditionTag, Tag
from ..models import (
    InterestRef,
    Listing,
    PriceReading,
    Verdict,
    WatchedItem,
    key_of,
)

DEFAULT_WATCHLIST_FILE = "private/watchlist.json"

# The shape written today. Reading remains backward-compatible with version 1.
FILE_VERSION = 2

ITEMS_KEY = "items"


@dataclass(frozen=True)
class FollowedListing:
    """One reportable lot and the interests that caused it to be followed.

    This small named record keeps the storage boundary readable. A bare tuple
    made it too easy for the pipeline and the watchlist to disagree as the
    information remembered about a match grew.
    """

    listing: Listing
    total_cost: Decimal
    matched_interests: tuple[InterestRef, ...] = ()


@dataclass(frozen=True)
class WatchlistStore:
    """One JSON file of followed lots, read and written whole."""

    path: Path

    def items(self) -> tuple[WatchedItem, ...]:
        """Every followed lot, in the order the file lists them."""
        if not self.path.exists():
            return ()
        document = read_json(self.path, default=None)
        if not isinstance(document, dict):
            raise ValueError(f"{self.path}: watchlist must be an object")
        version = document.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError(f"{self.path}: version must be 1 or {FILE_VERSION}")
        if version not in (1, FILE_VERSION):
            raise ValueError(
                f"{self.path}: unsupported watchlist version {version}; "
                "upgrade Auction Lens before writing this file"
            )
        rows = document.get(ITEMS_KEY)
        if not isinstance(rows, list):
            raise ValueError(f"{self.path}: items must be a list")
        items = tuple(self._read(row) for row in rows)
        _require_unambiguous_items(items, path=self.path)
        return items

    def get(self, source: str, identifier: str) -> WatchedItem | None:
        """One followed lot, found by either the item id or an auction id."""
        return next(
            (item for item in self.items() if item.answers_to(source, identifier)), None
        )

    def save(self, item: WatchedItem) -> None:
        """Add a lot, or replace the one already stored under the same item key."""
        kept = [
            stored for stored in self.items() if stored.item_key != item.item_key
        ]
        self._write([*kept, item])

    def drop(self, source: str, identifier: str) -> bool:
        """Stop following a lot; say whether there was one to stop following."""
        stored = self.items()
        kept = [item for item in stored if not item.answers_to(source, identifier)]
        if len(kept) == len(stored):
            return False
        self._write(kept)
        return True

    def record(self, seen: Iterable[FollowedListing]) -> int:
        """Refresh followed lots and say how many entries actually changed.

        The whole run is written once. A lot seen twice at the same instant --
        the same input file read twice, say -- leaves one reading, not two, but
        newly learned match provenance can still update that entry.
        """
        stored = {item.item_key: item for item in self.items()}
        touched = 0
        for followed in seen:
            listing = followed.listing
            item = stored.get(listing.item_key)
            updated = _observed(
                item,
                listing,
                followed.total_cost,
                followed.matched_interests,
            )
            if updated is not None:
                stored[updated.item_key] = updated
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
            # "uid" is what this field was called in files written earlier.
            name = (
                row.get("key") or row.get("uid") or row.get("listing_id") or "an item"
            )
            raise ValueError(f"{self.path}: {name}: {error}") from error


def _observed(
    item: WatchedItem | None,
    listing: Listing,
    total_cost: Decimal,
    matched_interests: tuple[InterestRef, ...],
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
        listing_id=listing.listing_id,
    )
    readings = () if item is None else item.readings
    if not any(stored.scanned_at == reading.scanned_at for stored in readings):
        readings = (*readings, reading)
    refreshed = WatchedItem(
        source=listing.source,
        listing_id=listing.listing_id,
        inventory_id=listing.inventory_id,
        title=listing.title,
        url=listing.url,
        photo_urls=listing.photo_urls,
        estimated_retail=listing.estimated_retail,
        conditions=() if listing.grade is None else listing.grade.tags,
        quality_rating=None if listing.grade is None else listing.grade.rating,
        my_estimate=None if item is None else item.my_estimate,
        verdict=Verdict.WATCHING if item is None else item.verdict,
        note="" if item is None else item.note,
        matched_interests=_merge_interest_refs(
            () if item is None else item.matched_interests,
            matched_interests,
        ),
        fulfilled_interests=() if item is None else item.fulfilled_interests,
        fulfillment_reviewed=False if item is None else item.fulfillment_reviewed,
        readings=readings,
    )
    return None if refreshed == item else refreshed


def _merge_interest_refs(
    stored: tuple[InterestRef, ...],
    observed: tuple[InterestRef, ...],
) -> tuple[InterestRef, ...]:
    """Keep stable order and identity, while refreshing display names."""
    merged = {ref.interest_id.casefold(): ref for ref in stored}
    for ref in observed:
        merged[ref.interest_id.casefold()] = ref
    return tuple(merged.values())


def _require_unambiguous_items(
    items: tuple[WatchedItem, ...], *, path: Path
) -> None:
    """Refuse identities that a read-modify-write could silently collapse.

    A hand-edited file may accidentally repeat one physical item, or give two
    items the same auction id. In either case ``get``, ``save``, and ``drop``
    could disagree about which row a command means. Failing at the read
    boundary keeps the original bytes intact until a person merges the rows.
    """
    seen: set[str] = set()
    lookup_keys: dict[tuple[str, str], str] = {}
    for item in items:
        if item.item_key in seen:
            raise ValueError(
                f"{path}: duplicate watchlist identity {item.item_key}; "
                "merge the duplicate entries before continuing"
            )
        seen.add(item.item_key)
        identifiers = {
            item.inventory_id,
            item.listing_id,
            *(reading.listing_id for reading in item.readings),
        }
        for identifier in identifiers - {""}:
            key = (item.source, identifier)
            prior = lookup_keys.get(key)
            if prior is not None and prior != item.item_key:
                raise ValueError(
                    f"{path}: watch key {key_of(*key)} refers to both "
                    f"{prior} and {item.item_key}; merge those entries "
                    "before continuing"
                )
            lookup_keys[key] = item.item_key


def _as_json(item: WatchedItem) -> dict[str, Any]:
    """Write money as text, so a rounded float can never become the record.

    ``key`` is written for a person reading or searching the file, and is the
    same spelling ``watch --key`` accepts. It is derived from source and
    listing id, so editing it in place changes nothing.
    """
    return {
        "key": item.key,
        "source": item.source,
        "listing_id": item.listing_id,
        "inventory_id": item.inventory_id,
        "title": item.title,
        "url": item.url,
        "photo_urls": list(item.photo_urls),
        "estimated_retail": _money(item.estimated_retail),
        "conditions": [_tag_as_json(tag) for tag in item.conditions],
        "quality_rating": item.quality_rating,
        "my_estimate": _money(item.my_estimate),
        "verdict": str(item.verdict),
        "note": item.note,
        "matched_interests": [
            _interest_ref_as_json(ref) for ref in item.matched_interests
        ],
        "fulfilled_interests": [
            _interest_ref_as_json(ref) for ref in item.fulfilled_interests
        ],
        "fulfillment_reviewed": item.fulfillment_reviewed,
        "readings": [_reading_as_json(reading) for reading in item.readings],
    }


def _reading_as_json(reading: PriceReading) -> dict[str, Any]:
    return {
        "scanned_at": reading.scanned_at.isoformat(),
        "current_bid": str(reading.current_bid),
        "total_cost": str(reading.total_cost),
        "bid_count": reading.bid_count,
        "listing_id": reading.listing_id,
    }


def _from_json(row: dict[str, Any]) -> WatchedItem:
    """Hold a hand-edited file to the same standard as any other input."""
    fulfilled_interests = _interest_refs_from_json(
        row.get("fulfilled_interests", []), field_name="fulfilled_interests"
    )
    return WatchedItem(
        source=str(row["source"]),
        listing_id=str(row["listing_id"]),
        inventory_id=str(row.get("inventory_id", "")),
        title=str(row.get("title", "")),
        url=str(row.get("url", "")),
        photo_urls=tuple(str(url) for url in row.get("photo_urls", [])),
        estimated_retail=parse_optional_money(
            row.get("estimated_retail"), field_name="estimated_retail"
        ),
        my_estimate=parse_optional_money(row.get("my_estimate"), field_name="my_estimate"),
        conditions=tuple(_tag_from_json(entry) for entry in row.get("conditions", [])),
        quality_rating=_optional_rating(row.get("quality_rating")),
        verdict=row.get("verdict", Verdict.WATCHING),
        note=str(row.get("note", "")),
        matched_interests=_interest_refs_from_json(
            row.get("matched_interests", []), field_name="matched_interests"
        ),
        fulfilled_interests=fulfilled_interests,
        fulfillment_reviewed=_fulfillment_reviewed_from_json(
            row, fulfilled_interests
        ),
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
        listing_id=str(entry.get("listing_id", "")),
    )


def _interest_ref_as_json(ref: InterestRef) -> dict[str, str]:
    return {"id": ref.interest_id, "name": ref.name}


def _interest_refs_from_json(value: Any, *, field_name: str) -> tuple[InterestRef, ...]:
    """Read the human-editable collection with errors that point to one field."""
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    refs: list[InterestRef] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValueError(f"{field_name}[{index}] must be an object")
        if "id" not in entry:
            raise ValueError(f"{field_name}[{index}].id is required")
        if "name" not in entry:
            raise ValueError(f"{field_name}[{index}].name is required")
        if not isinstance(entry["id"], str):
            raise ValueError(f"{field_name}[{index}].id must be text")
        if not isinstance(entry["name"], str):
            raise ValueError(f"{field_name}[{index}].name must be text")
        try:
            refs.append(InterestRef(interest_id=entry["id"], name=entry["name"]))
        except ValueError as error:
            raise ValueError(f"{field_name}[{index}]: {error}") from error
    return tuple(refs)


def _fulfillment_reviewed_from_json(
    row: dict[str, Any], fulfilled_interests: tuple[InterestRef, ...]
) -> bool:
    """Read the explicit answer while understanding older allocated entries."""
    if "fulfillment_reviewed" not in row:
        return bool(fulfilled_interests)
    reviewed = row["fulfillment_reviewed"]
    if not isinstance(reviewed, bool):
        raise ValueError("fulfillment_reviewed must be true or false")
    return reviewed or bool(fulfilled_interests)


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _tag_as_json(tag: ConditionTag) -> dict[str, Any]:
    """Keep the axis beside the answer, so a report can say what was asked."""
    return {"axis": tag.axis, "label": tag.label, "tag": str(tag.tag)}


def _tag_from_json(entry: dict[str, Any]) -> ConditionTag:
    return ConditionTag(
        axis=str(entry.get("axis", "")),
        label=str(entry.get("label", "")),
        tag=Tag(str(entry.get("tag", Tag.AMBER))),
    )


def _optional_rating(value: Any) -> int | None:
    """A provider that does not rate its lots leaves this out entirely."""
    return None if value is None else parse_whole_number(value, field_name="quality_rating")
