"""Private receipts for reports that a destination has already accepted.

Observation history answers what the scanner saw.  This separate database
answers a narrower question: which revision did one delivery route actually
receive?  Keeping that distinction lets a failed email retry without making a
listing look newly observed, and lets email and webhooks succeed independently.

Only opaque destination and summary fingerprints are stored.  The database
never receives an address, webhook URL, listing title, listing URL, or photo.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ..notifications import (
    LOWERCASE_HEX,
    SHA256_HEX_LENGTH,
    DeliveryChannel,
    DeliveryItem,
    DeliveryRoute,
    ReportKind,
)

DEFAULT_DELIVERY_LEDGER = "private/deliveries.sqlite3"

# A second scheduled run deliberately waits while the first one is sending.
# SMTP gets 30 seconds, so this lock must outlive that attempt before giving up.
DELIVERY_LOCK_TIMEOUT_SECONDS = 45.0

_SCHEMA_VERSION = 1
_ITEMS_TABLE = "delivery_items"
_SUMMARIES_TABLE = "delivery_summaries"

_CREATE_ITEMS = f"""
CREATE TABLE IF NOT EXISTS {_ITEMS_TABLE} (
    report_kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    destination_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (
        report_kind, channel, destination_hash, source, listing_id
    )
)
"""

_CREATE_SUMMARIES = f"""
CREATE TABLE IF NOT EXISTS {_SUMMARIES_TABLE} (
    report_kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    destination_hash TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (report_kind, channel, destination_hash)
)
"""

_SELECT_REVISIONS = f"""
SELECT source, listing_id, revision
FROM {_ITEMS_TABLE}
WHERE report_kind = ? AND channel = ? AND destination_hash = ?
"""

_SELECT_SUMMARY = f"""
SELECT fingerprint
FROM {_SUMMARIES_TABLE}
WHERE report_kind = ? AND channel = ? AND destination_hash = ?
"""

_UPSERT_ITEM = f"""
INSERT INTO {_ITEMS_TABLE} (
    report_kind, channel, destination_hash,
    source, listing_id, revision, delivered_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(report_kind, channel, destination_hash, source, listing_id)
DO UPDATE SET
    revision = excluded.revision,
    delivered_at = excluded.delivered_at
"""

_UPSERT_SUMMARY = f"""
INSERT INTO {_SUMMARIES_TABLE} (
    report_kind, channel, destination_hash, fingerprint, delivered_at
) VALUES (?, ?, ?, ?, ?)
ON CONFLICT(report_kind, channel, destination_hash)
DO UPDATE SET
    fingerprint = excluded.fingerprint,
    delivered_at = excluded.delivered_at
"""

_EXPECTED_COLUMNS = {
    _ITEMS_TABLE: (
        ("report_kind", "TEXT", True, 1),
        ("channel", "TEXT", True, 2),
        ("destination_hash", "TEXT", True, 3),
        ("source", "TEXT", True, 4),
        ("listing_id", "TEXT", True, 5),
        ("revision", "TEXT", True, 0),
        ("delivered_at", "TEXT", True, 0),
    ),
    _SUMMARIES_TABLE: (
        ("report_kind", "TEXT", True, 1),
        ("channel", "TEXT", True, 2),
        ("destination_hash", "TEXT", True, 3),
        ("fingerprint", "TEXT", True, 0),
        ("delivered_at", "TEXT", True, 0),
    ),
}


class DeliveryLedger:
    """One private SQLite file containing successful-delivery receipts."""

    def __init__(self, path: str | Path = DEFAULT_DELIVERY_LEDGER):
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError("delivery ledger path must be text or a path")
        self.path = Path(path)

    def check_ready(self) -> None:
        """Verify this ledger can be used, without creating or changing it."""
        if not _check_location(self.path):
            return

        try:
            with closing(_connect_read_only(self.path)) as connection:
                _validate_schema(connection, self.path)
        except sqlite3.Error as error:
            raise ValueError(
                f"{self.path}: delivery ledger is not a valid SQLite database: {error}"
            ) from error

    @contextmanager
    def session(self, route: DeliveryRoute) -> Iterator[DeliverySession]:
        """Serialize one route decision and commit only an accepted report.

        The immediate transaction stays open while the caller performs the
        transport.  A concurrent run therefore waits, then reads the receipt
        written by the first run instead of sending the same report twice.
        """
        route_key = _validated_route(route)
        # Schema validation happens only after taking the write lock. On the
        # very first run, another process may have created the file but not yet
        # committed its schema; reading it early would mistake that brief state
        # for a corrupt ledger instead of waiting for the first run.
        existed = _check_location(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with closing(
            sqlite3.connect(self.path, timeout=DELIVERY_LOCK_TIMEOUT_SECONDS)
        ) as connection:
            connection.execute(
                f"PRAGMA busy_timeout = {int(DELIVERY_LOCK_TIMEOUT_SECONDS * 1000)}"
            )
            try:
                connection.execute("BEGIN IMMEDIATE")
                if existed:
                    _validate_schema(connection, self.path)
                else:
                    _initialize(connection)
                    connection.commit()
                    connection.execute("BEGIN IMMEDIATE")

                delivery = DeliverySession(connection, route_key)
                try:
                    yield delivery
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    if delivery.accepted:
                        connection.commit()
                    else:
                        connection.rollback()
            except sqlite3.Error as error:
                connection.rollback()
                raise ValueError(
                    f"{self.path}: could not use delivery ledger: {error}"
                ) from error


class DeliverySession:
    """The delivery receipts visible to one locked report route."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        route_key: tuple[str, str, str],
    ):
        self._connection = connection
        self._route_key = route_key
        self._accepted = False

    @property
    def accepted(self) -> bool:
        """Whether this transaction contains an accepted report."""
        return self._accepted

    def revisions(self, items: Iterable[DeliveryItem]) -> dict[tuple[str, str], str]:
        """Return prior revisions for the requested listing events."""
        requested = _validated_items(items)
        if not requested:
            return {}
        rows = self._connection.execute(_SELECT_REVISIONS, self._route_key)
        return {
            (source, listing_id): revision
            for source, listing_id, revision in rows
            if (source, listing_id) in requested
        }

    def summary_changed(self, fingerprint: str) -> bool:
        """Say whether this route has accepted a different report summary."""
        _require_digest(fingerprint, field_name="summary fingerprint")
        row = self._connection.execute(_SELECT_SUMMARY, self._route_key).fetchone()
        return row is None or row[0] != fingerprint

    def accept(
        self,
        items: Iterable[DeliveryItem],
        summary_fingerprint: str,
        delivered_at: datetime | None = None,
    ) -> None:
        """Stage receipts for exactly the events the transport accepted."""
        if self._accepted:
            raise RuntimeError("this delivery session has already been accepted")
        selected = _validated_items(items)
        _require_digest(summary_fingerprint, field_name="summary fingerprint")
        timestamp = _delivery_timestamp(delivered_at)

        for item in selected.values():
            self._connection.execute(
                _UPSERT_ITEM,
                (*self._route_key, item.source, item.listing_id, item.revision, timestamp),
            )
        self._connection.execute(
            _UPSERT_SUMMARY,
            (*self._route_key, summary_fingerprint, timestamp),
        )
        self._accepted = True


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute(_CREATE_ITEMS)
    connection.execute(_CREATE_SUMMARIES)
    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _validate_schema(connection: sqlite3.Connection, path: Path) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > _SCHEMA_VERSION:
        raise ValueError(
            f"{path}: delivery ledger version {version} is newer than supported "
            f"version {_SCHEMA_VERSION}; upgrade Auction Lens before using it"
        )
    if version != _SCHEMA_VERSION:
        raise ValueError(
            f"{path}: delivery ledger has schema version {version}; expected "
            f"version {_SCHEMA_VERSION}"
        )

    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if table_names != set(_EXPECTED_COLUMNS):
        raise ValueError(f"{path}: delivery ledger tables do not match version 1")

    for table, expected in _EXPECTED_COLUMNS.items():
        actual = tuple(
            (row[1], row[2].upper(), bool(row[3]), row[5])
            for row in connection.execute(f"PRAGMA table_info({table})")
        )
        if actual != expected:
            raise ValueError(f"{path}: delivery ledger table {table} does not match version 1")

    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if integrity != ("ok",):
        detail = "unknown error" if integrity is None else str(integrity[0])
        raise ValueError(f"{path}: delivery ledger integrity check failed: {detail}")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _nearest_existing_parent(path: Path) -> Path:
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    return ancestor


def _check_location(path: Path) -> bool:
    """Validate the path itself and say whether a ledger already exists."""
    if not path.exists():
        ancestor = _nearest_existing_parent(path)
        if not ancestor.is_dir():
            raise ValueError(f"{path}: delivery ledger parent {ancestor} is not a directory")
        if not os.access(ancestor, os.W_OK | os.X_OK):
            raise ValueError(f"{path}: delivery ledger parent {ancestor} is not writable")
        return False
    if not path.is_file():
        raise ValueError(f"{path}: delivery ledger must be a file")
    if not os.access(path, os.R_OK | os.W_OK):
        raise ValueError(f"{path}: delivery ledger is not readable and writable")
    if not os.access(path.parent, os.W_OK | os.X_OK):
        raise ValueError(f"{path}: delivery ledger parent {path.parent} is not writable")
    return True


def _validated_route(route: DeliveryRoute) -> tuple[str, str, str]:
    if not isinstance(route, DeliveryRoute):
        raise TypeError("route must be a DeliveryRoute")
    if not isinstance(route.report_kind, ReportKind):
        raise TypeError("route report_kind must be a ReportKind")
    if not isinstance(route.channel, DeliveryChannel):
        raise TypeError("route channel must be a DeliveryChannel")
    _require_digest(
        route.destination_fingerprint,
        field_name="destination fingerprint",
    )
    return (
        route.report_kind.value,
        route.channel.value,
        route.destination_fingerprint,
    )


def _validated_items(
    items: Iterable[DeliveryItem],
) -> dict[tuple[str, str], DeliveryItem]:
    if isinstance(items, (str, bytes)):
        raise TypeError("delivery items must be an iterable of DeliveryItem values")
    try:
        iterator = iter(items)
    except TypeError as error:
        raise TypeError("delivery items must be an iterable of DeliveryItem values") from error

    unique: dict[tuple[str, str], DeliveryItem] = {}
    for index, item in enumerate(iterator):
        if not isinstance(item, DeliveryItem):
            raise TypeError(f"delivery item {index} must be a DeliveryItem")
        _require_text(item.source, field_name=f"delivery item {index} source")
        _require_text(item.listing_id, field_name=f"delivery item {index} listing_id")
        _require_text(item.revision, field_name=f"delivery item {index} revision")
        previous = unique.get(item.key)
        if previous is not None and previous.revision != item.revision:
            raise ValueError(
                f"delivery items contain conflicting revisions for "
                f"{item.source}/{item.listing_id}"
            )
        unique[item.key] = item
    return unique


def _require_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_digest(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    if len(value) != SHA256_HEX_LENGTH or not set(value) <= LOWERCASE_HEX:
        raise ValueError(f"{field_name} must be a full lowercase SHA-256 digest")


def _delivery_timestamp(value: datetime | None) -> str:
    instant = datetime.now(UTC) if value is None else value
    if not isinstance(instant, datetime):
        raise TypeError("delivered_at must be a datetime")
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("delivered_at must be timezone-aware")
    return instant.astimezone(UTC).isoformat()
