"""The profile readback explains configuration without becoming another authority."""

from __future__ import annotations

import unittest
from dataclasses import replace

from auction_lens.config import LocationPolicy, ReportsConfig, render_profile
from support import example_config


class ProfileRenderingTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()

    def test_it_explains_interests_and_their_resolved_condition_policies(self):
        rendered = render_profile(self.config)

        self.assertIn("1. soundbar - purpose: use", rendered)
        self.assertIn('Match any: "soundbar", "sound bar"', rendered)
        self.assertIn("Maximum total cost: $50.00", rendered)
        self.assertIn("Conditions (ready_to_use): unknown accepted", rendered)
        self.assertIn('reject "not functional", "parts only"', rendered)
        self.assertIn("missing parts (-45)", rendered)

    def test_it_states_the_effective_general_location_and_handling_rules(self):
        rendered = render_profile(self.config)

        self.assertIn("stated retail at least $100.00", rendered)
        self.assertIn("total cost at most 20%", rendered)
        self.assertIn("Allowed: any pickup location", rendered)
        self.assertIn("Large items: ask for a handling plan", rendered)
        self.assertIn("published weight over 75 lb", rendered)

    def test_empty_and_unlimited_states_are_said_out_loud(self):
        config = replace(
            self.config,
            interests=(),
            locations=LocationPolicy(),
            reports=ReportsConfig(),
            valuation=replace(self.config.valuation, enabled=False),
        )
        rendered = render_profile(config)

        self.assertIn("No interests configured", rendered)
        self.assertIn("Allowed: any pickup location", rendered)
        self.assertIn("Far locations (minimum score 90): none", rendered)
        self.assertIn("Length: all matches", rendered)
        self.assertIn("Valuation: off", rendered)

    def test_it_does_not_expose_credentials_or_adapter_details(self):
        private_marker = "PRIVATE-SENTINEL-DO-NOT-PRINT"
        sources = tuple(
            replace(
                source,
                settings={**source.settings, "private_detail": private_marker},
            )
            for source in self.config.valuation.sources
        )
        config = replace(
            self.config,
            valuation=replace(self.config.valuation, sources=sources),
            email=replace(self.config.email, password_env=f"{private_marker}-PASSWORD"),
            webhook=replace(self.config.webhook, url_env=f"{private_marker}-WEBHOOK"),
        )

        self.assertNotIn(private_marker, render_profile(config))

    def test_output_is_deterministic_plain_text(self):
        first = render_profile(self.config)
        second = render_profile(self.config)

        self.assertEqual(first, second)
        self.assertNotIn("\x1b", first)
        self.assertTrue(first.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
