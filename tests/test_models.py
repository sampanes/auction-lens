"""Records that refuse to exist in a state no consumer could act on."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from auction_lens.models import (
    KEY_SEPARATOR,
    ClosingPrice,
    Listing,
    LogisticsDecision,
    LogisticsStatus,
    ValuationObservation,
    WatchedItem,
    key_of,
)


class LogisticsDecisionTests(unittest.TestCase):
    def test_the_stored_word_becomes_the_member_it_names(self):
        """SQLite and argparse both hand us text, and both must end up here."""
        decision = LogisticsDecision(status="feasible")
        self.assertIs(decision.status, LogisticsStatus.FEASIBLE)

    def test_a_status_an_operator_may_not_record_is_refused(self):
        with self.assertRaisesRegex(ValueError, "must be one of: feasible, infeasible"):
            LogisticsDecision(status=LogisticsStatus.NEEDS_PLAN)

    def test_an_unknown_status_is_refused(self):
        with self.assertRaisesRegex(ValueError, "must be one of: feasible, infeasible"):
            LogisticsDecision(status="probably")

    def test_a_decision_cannot_add_a_negative_cost(self):
        with self.assertRaisesRegex(ValueError, "added_cost cannot be negative"):
            LogisticsDecision(status="feasible", added_cost=Decimal("-5"))


class ValuationObservationTests(unittest.TestCase):
    def test_a_band_out_of_order_is_refused_wherever_it_came_from(self):
        with self.assertRaisesRegex(ValueError, "low <= typical <= high"):
            ValuationObservation(
                source_id="anywhere",
                basis="used_sold",
                low=Decimal("50"),
                typical=Decimal("200"),
                high=Decimal("150"),
            )

    def test_an_empty_sample_is_refused_rather_than_quietly_counted_as_one(self):
        with self.assertRaisesRegex(ValueError, "sample_size must be at least 1"):
            ValuationObservation(
                source_id="anywhere",
                basis="used_sold",
                low=Decimal("50"),
                typical=Decimal("100"),
                high=Decimal("150"),
                sample_size=0,
            )

    def test_non_finite_prices_are_refused(self):
        with self.assertRaisesRegex(ValueError, "low must be a finite number"):
            ValuationObservation(
                source_id="anywhere",
                basis="used_sold",
                low=Decimal("NaN"),
                typical=Decimal("100"),
                high=Decimal("150"),
            )

    def test_non_finite_confidence_is_refused(self):
        with self.assertRaisesRegex(ValueError, "confidence must be a finite number"):
            ValuationObservation(
                source_id="anywhere",
                basis="used_sold",
                low=Decimal("50"),
                typical=Decimal("100"),
                high=Decimal("150"),
                confidence=Decimal("Infinity"),
            )


class NamingOneLotTests(unittest.TestCase):
    """Everything a person can paste is spelled exactly one way.

    There were once two separators a single character apart -- a colon for the
    storage identity and a slash for the key a person types. A record printed
    with the wrong one still looked like a key, so the mistake read as correct
    and survived. One separator means it cannot happen again.
    """

    def _listing(self, **changes) -> Listing:
        fields = {
            "source": "nellis",
            "listing_id": "auction-2",
            "title": "Example",
            "url": "https://example.test/2",
            "current_bid": Decimal("1"),
        }
        return Listing(**{**fields, **changes})

    def test_a_listing_names_the_auction_it_is_in(self):
        self.assertEqual(self._listing().key, "nellis/auction-2")

    def test_a_listing_names_the_thing_by_its_item_id_when_there_is_one(self):
        listing = self._listing(inventory_id="INV-77")
        self.assertEqual(listing.item_key, "nellis/INV-77")
        self.assertEqual(listing.key, "nellis/auction-2")

    def test_a_provider_with_no_item_id_names_the_thing_by_its_auction(self):
        self.assertEqual(self._listing().item_key, "nellis/auction-2")

    def test_a_followed_lot_spells_both_names_the_same_way(self):
        item = WatchedItem(source="nellis", listing_id="auction-2", inventory_id="INV-77")
        self.assertEqual(item.key, "nellis/auction-2")
        self.assertEqual(item.item_key, "nellis/INV-77")

    def test_every_record_that_names_a_lot_uses_the_one_separator(self):
        # The point of the change: pasting any of these works, because none of
        # them can be told apart by shape.
        listing = self._listing(inventory_id="INV-77")
        item = WatchedItem(source="nellis", listing_id="auction-2", inventory_id="INV-77")
        price = ClosingPrice(
            source="nellis",
            listing_id="auction-2",
            title="Example",
            url="https://example.test/2",
            last_bid=Decimal("1"),
            ends_at=datetime(2026, 9, 11, 20, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 9, 11, 19, 30, tzinfo=UTC),
        )
        spellings = {listing.key, listing.item_key, item.key, item.item_key, price.key}
        self.assertTrue(
            all(name.count(KEY_SEPARATOR) == 1 for name in spellings), spellings
        )
        self.assertTrue(all(name.startswith("nellis/") for name in spellings), spellings)

    def test_the_separator_is_written_down_once(self):
        self.assertEqual(key_of("nellis", "auction-2"), f"nellis{KEY_SEPARATOR}auction-2")


if __name__ == "__main__":
    unittest.main()
