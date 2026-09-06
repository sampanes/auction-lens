"""Records that refuse to exist in a state no consumer could act on."""

from __future__ import annotations

import unittest
from decimal import Decimal

from auction_lens.models import (
    LogisticsDecision,
    LogisticsStatus,
    ValuationObservation,
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


if __name__ == "__main__":
    unittest.main()
