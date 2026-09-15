"""Old public data files remain useful after the implementation moves around."""

from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal

from auction_lens.history.database import Database
from auction_lens.history.logistics import LogisticsDecisionStore
from auction_lens.history.observations import ObservationStore
from auction_lens.listings.model import Listing
from auction_lens.matching.logistics import LogisticsDecision
from auction_lens.watchlist.model import Verdict
from auction_lens.watchlist.store import WatchlistStore
from support import ROOT, temporary_directory

COMPATIBILITY_FIXTURES = ROOT / "fixtures" / "compatibility"


class WatchlistCompatibilityTests(unittest.TestCase):
    def test_version_one_keeps_the_persons_decisions_when_rewritten(self):
        path, item = self._load("watchlist-v1.json")

        self.assertEqual(item.verdict, Verdict.WON)
        self.assertEqual(item.my_estimate, Decimal("42.50"))
        self.assertEqual(item.note, "keep this human decision")
        self.assertEqual(item.photo_urls[-1], "https://example.invalid/photo/legacy-condition.jpg")
        self.assertEqual(item.latest.total_cost, Decimal("11.50"))

        WatchlistStore(path).save(item)
        rewritten = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rewritten["version"], 2)
        self.assertEqual(rewritten["items"][0]["verdict"], "won")
        self.assertEqual(rewritten["items"][0]["my_estimate"], "42.50")
        self.assertEqual(rewritten["items"][0]["note"], "keep this human decision")

    def test_version_two_keeps_item_identity_allocations_and_relisting_history(self):
        path, item = self._load("watchlist-v2.json")

        self.assertEqual(item.key, "synthetic/legacy-auction-2")
        self.assertEqual(item.item_key, "synthetic/legacy-item-1")
        self.assertEqual(item.auctions_seen, 2)
        self.assertEqual(item.fulfilled_interests[0].interest_id, "audio")
        self.assertTrue(item.fulfillment_reviewed)

        WatchlistStore(path).save(item)
        (rewritten,) = WatchlistStore(path).items()
        self.assertEqual(rewritten, item)

    def _load(self, fixture_name: str):
        fixture = COMPATIBILITY_FIXTURES / fixture_name
        temporary = self.enterContext(temporary_directory())
        path = temporary / "watchlist.json"
        path.write_bytes(fixture.read_bytes())
        (item,) = WatchlistStore(path).items()
        return path, item


class ObservationDatabaseCompatibilityTests(unittest.TestCase):
    def test_an_existing_database_keeps_history_and_accepts_a_new_observation(self):
        with temporary_directory() as directory:
            path = directory / "observations.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.executescript(
                        (COMPATIBILITY_FIXTURES / "observations-v0.sql").read_text(
                            encoding="utf-8"
                        )
                    )

            database = Database.at(path)
            database.initialize()
            handling = LogisticsDecisionStore(database).get(
                "synthetic", "legacy-auction-1"
            )
            change = ObservationStore(database).observe(_updated_legacy_listing())

            with database.connect() as connection:
                listing = connection.execute(
                    """
                    SELECT current_bid, typeof(current_bid), first_seen, last_seen
                    FROM listings
                    WHERE source = 'synthetic' AND listing_id = 'legacy-auction-1'
                    """
                ).fetchone()
                history = connection.execute(
                    """
                    SELECT current_bid, typeof(current_bid)
                    FROM price_history
                    WHERE source = 'synthetic' AND listing_id = 'legacy-auction-1'
                    ORDER BY observed_at
                    """
                ).fetchall()

        self.assertEqual(
            handling,
            LogisticsDecision(
                status="feasible",
                added_cost=Decimal("12.50"),
                note="borrow a generic cart",
            ),
        )
        self.assertTrue(change.price_changed)
        self.assertEqual(change.previous_bid, Decimal("10.00"))
        self.assertEqual(
            listing,
            (
                "20.00",
                "text",
                "2026-08-01T12:00:00+00:00",
                "2026-08-02T12:00:00+00:00",
            ),
        )
        self.assertEqual(history, [("10.00", "text"), ("20.00", "text")])


def _updated_legacy_listing() -> Listing:
    return Listing(
        source="synthetic",
        listing_id="legacy-auction-1",
        title="Legacy Example Speaker",
        url="https://example.invalid/auction/legacy-auction-1",
        current_bid=Decimal("20.00"),
        estimated_retail=Decimal("125.00"),
        bid_count=3,
        location="Example Branch",
        conditions=("used",),
        photo_urls=("https://example.invalid/photo/legacy-condition.jpg",),
        observed_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
    )


if __name__ == "__main__":
    unittest.main()
