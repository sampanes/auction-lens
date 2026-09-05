"""Loading and validating a provider configuration file."""

from __future__ import annotations

import unittest
from decimal import Decimal

from auction_lens.config import load_config
from support import EXAMPLE_CONFIG, example_config, temporary_directory


class ExampleConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()

    def test_provider_identity_is_separate_from_its_economics(self):
        self.assertEqual(self.config.provider.provider_id, "nellis")
        self.assertEqual(self.config.provider.display_name, "Nellis Auction")
        self.assertEqual(self.config.economics.buyer_premium_rate, Decimal("0.15"))
        self.assertTrue(self.config.economics.premium_is_taxable)

    def test_reusable_condition_profile_is_loaded(self):
        soundbar = next(rule for rule in self.config.interests if rule.name == "soundbar")
        self.assertEqual(soundbar.condition_profile, "ready_to_use")
        self.assertIn("not functional", soundbar.condition.reject)
        self.assertEqual(soundbar.condition.penalties["untested"], 22)

    def test_anomaly_discovery_reuses_the_same_profile(self):
        self.assertIn("parts only", self.config.scoring.anomaly_condition.reject)


class ConfigValidationTests(unittest.TestCase):
    def test_invalid_email_security_is_rejected_at_load_time(self):
        with self.assertRaisesRegex(ValueError, "email security must be"):
            self._load_variant('security = "ssl"', 'security = "starttlz"')

    def test_unknown_condition_profile_names_the_missing_profile(self):
        with self.assertRaisesRegex(ValueError, "unknown condition profile 'typo'"):
            self._load_variant(
                'condition_profile = "ready_to_use"',
                'condition_profile = "typo"',
            )

    def test_duplicate_valuation_source_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            self._load_variant('id = "music-gear-research"', 'id = "reviewed-comps"')

    def test_unusable_valuation_weight_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            self._load_variant("weight = 1.0", "weight = 0")

    def test_large_item_policy_must_be_one_of_three_words(self):
        with self.assertRaisesRegex(ValueError, "ask, allow, or reject"):
            self._load_variant('large_item_policy = "ask"', 'large_item_policy = "maybe"')

    def test_renamed_interests_table_is_explained(self):
        with self.assertRaisesRegex(ValueError, "renamed to"):
            self._load_variant("[[interests]]", "[[wanted]]")

    def _load_variant(self, original: str, replacement: str):
        """Load the example configuration with one setting changed."""
        source = EXAMPLE_CONFIG.read_text(encoding="utf-8")
        self.assertIn(original, source)
        with temporary_directory() as directory:
            variant = directory / "variant.toml"
            variant.write_text(source.replace(original, replacement), encoding="utf-8")
            return load_config(variant)


if __name__ == "__main__":
    unittest.main()
