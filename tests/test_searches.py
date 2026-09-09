"""Phrases to paste into the provider's search bar."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from auction_lens.config import InterestRule
from auction_lens.reporting import search_hints
from auction_lens.reporting.searches import (
    FEWEST_LOTS_WORTH_A_PHRASE,
    MOST_STRANGERS_PER_LOT,
)
from auction_lens.scoring import evaluate
from support import SOUNDBAR, example_config, example_listings


class SearchHintTests(unittest.TestCase):
    """Reaching a whole category at the provider's end, in as few pastes as
    possible, with an honest count of what each one drags in."""

    def setUp(self):
        self.config = example_config()
        self.template = example_listings()[SOUNDBAR]

    def _lot(self, index, title):
        return replace(
            self.template,
            listing_id=f"lot-{index}",
            title=title,
            estimated_retail=Decimal("300"),
        )

    def _hints(self, rule, titles, strangers=()):
        listings = [self._lot(i, title) for i, title in enumerate(titles)]
        config = replace(self.config, interests=(rule,))
        candidates = [
            candidate
            for listing in listings
            for candidate in evaluate(listing, config)
            if candidate.category == "wanted"
        ]
        everything = listings + [
            self._lot(1000 + i, title) for i, title in enumerate(strangers)
        ]
        return search_hints(candidates, everything, (rule,)), candidates

    def test_a_handful_of_lots_is_not_worth_a_phrase(self):
        # Below the threshold the links in the report are the shorter path.
        rule = InterestRule(name="bounce house", any_terms=("water slide",))
        titles = ["Inflatable Water Slide"] * (FEWEST_LOTS_WORTH_A_PHRASE - 1)
        hints, candidates = self._hints(rule, titles)
        self.assertEqual(len(candidates), FEWEST_LOTS_WORTH_A_PHRASE - 1)
        self.assertEqual(hints, ())

    def test_every_matched_lot_is_reachable_by_some_phrase(self):
        rule = InterestRule(
            name="bounce house", any_terms=("water slide", "splash pool")
        )
        titles = [
            "Inflatable Water Slide for Kids",
            "Bounwell Inflatable Water Slide",
            "Causeair Water Slide with Bounce House",
            "Easyair Inflatable Bounce House Splash Pool",
        ]
        hints, candidates = self._hints(rule, titles)
        self.assertEqual({hint.rule for hint in hints}, {"bounce house"})
        reached = {
            candidate.listing.listing_id
            for candidate in candidates
            for hint in hints
            if hint.phrase in candidate.listing.title.lower()
        }
        self.assertEqual(len(reached), len(candidates))

    def test_a_phrase_says_how_much_else_it_will_surface(self):
        rule = InterestRule(name="bounce house", any_terms=("water slide",))
        hints, _ = self._hints(
            rule,
            ["Inflatable Water Slide"] * FEWEST_LOTS_WORTH_A_PHRASE,
            strangers=["VEVOR Water Slide Pool Liner", "Water Slide Repair Patch"],
        )
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].finds, FEWEST_LOTS_WORTH_A_PHRASE)
        self.assertEqual(hints[0].also_finds, 2)

    def test_a_phrase_that_drags_in_a_crowd_is_not_offered(self):
        # Scrolling that is worse than opening the one link it would have
        # saved, so the lot keeps its link and the phrase is left out.
        rule = InterestRule(name="car seat", any_terms=("car seat",))
        titles = ["Graco Car Seat"] * FEWEST_LOTS_WORTH_A_PHRASE
        crowd = [
            f"Unrelated Car Seat Cover {index}"
            for index in range(FEWEST_LOTS_WORTH_A_PHRASE * MOST_STRANGERS_PER_LOT + 1)
        ]
        hints, _ = self._hints(rule, titles, strangers=crowd)
        self.assertEqual(hints, ())


if __name__ == "__main__":
    unittest.main()
