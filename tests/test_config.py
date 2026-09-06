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
        self.assertEqual(self.config.economics.default_buyer_premium, Decimal("0.15"))
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
        with self.assertRaisesRegex(ValueError, "security must be one of: ssl, starttls"):
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
        with self.assertRaisesRegex(
            ValueError, "large_item_policy must be one of: ask, allow, reject"
        ):
            self._load_variant('large_item_policy = "ask"', 'large_item_policy = "maybe"')

    def test_renamed_interests_table_is_explained(self):
        with self.assertRaisesRegex(ValueError, "renamed to"):
            self._load_variant("[[interests]]", "[[wanted]]")

    def test_a_negative_fee_is_rejected_by_the_key_that_holds_it(self):
        with self.assertRaisesRegex(ValueError, "economics: processing_fee cannot be negative"):
            self._load_variant("processing_fee = 0.0", "processing_fee = -5")

    def test_a_setting_of_the_wrong_type_names_its_key(self):
        with self.assertRaisesRegex(ValueError, "scoring.minimum_report_score"):
            self._load_variant("minimum_report_score = 70", 'minimum_report_score = "high"')

    def test_a_quoted_boolean_is_not_treated_as_true(self):
        with self.assertRaisesRegex(ValueError, "provider.enabled must be true or false"):
            self._load_variant("enabled = true", 'enabled = "false"')

    def test_a_fractional_integer_is_not_silently_truncated(self):
        with self.assertRaisesRegex(
            ValueError, "scoring.minimum_report_score must be a whole number"
        ):
            self._load_variant("minimum_report_score = 70", "minimum_report_score = 70.5")

    def test_non_finite_numbers_are_rejected_by_key(self):
        with self.assertRaisesRegex(
            ValueError, "economics.processing_fee must be a finite number"
        ):
            self._load_variant("processing_fee = 0.0", "processing_fee = inf")

    def test_text_settings_do_not_accept_tables_or_arrays(self):
        with self.assertRaisesRegex(ValueError, "provider.display_name must be text"):
            self._load_variant(
                'display_name = "Nellis Auction"',
                'display_name = ["Nellis Auction"]',
            )

    def test_empty_matching_terms_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError, r"interests\[0\]\.any_terms\[0\] must be non-empty text"
        ):
            self._load_variant(
                'any_terms = ["soundbar", "sound bar"]',
                'any_terms = ["", "sound bar"]',
            )

    def test_a_negative_interest_budget_is_rejected_by_key(self):
        with self.assertRaisesRegex(
            ValueError, r"interests\[0\]: max_total_cost cannot be negative"
        ):
            self._load_variant("max_total_cost = 50", "max_total_cost = -1")

    def test_request_safeguards_cannot_be_negative(self):
        with self.assertRaisesRegex(
            ValueError, "provider.acquisition: minimum_interval_minutes cannot be negative"
        ):
            self._load_variant(
                'mode = "authorized_http"',
                'mode = "authorized_http"\nminimum_interval_minutes = -1',
            )

    def test_an_unknown_time_zone_names_the_setting(self):
        with self.assertRaisesRegex(
            ValueError, "provider.acquisition: timezone must be a valid IANA time zone"
        ):
            self._load_variant(
                'mode = "authorized_http"',
                'mode = "authorized_http"\ntimezone = "Moon/SeaOfTranquility"',
            )

    def test_scores_stay_inside_the_reported_range(self):
        with self.assertRaisesRegex(
            ValueError, "scoring: minimum_report_score must be between 0 and 100"
        ):
            self._load_variant("minimum_report_score = 70", "minimum_report_score = 101")

    def test_anomaly_ratio_cannot_describe_a_markup(self):
        with self.assertRaisesRegex(
            ValueError, "scoring: anomaly_maximum_ratio cannot exceed 1"
        ):
            self._load_variant("anomaly_maximum_ratio = 0.20", "anomaly_maximum_ratio = 1.01")

    def test_penalties_cannot_turn_into_bonuses(self):
        with self.assertRaisesRegex(
            ValueError, "condition_profiles.ready_to_use.penalties.untested cannot be negative"
        ):
            self._load_variant('"untested" = 22', '"untested" = -1')

    def test_email_port_is_in_the_tcp_port_range(self):
        with self.assertRaisesRegex(
            ValueError, "reports.email: port must be between 1 and 65535"
        ):
            self._load_variant("port = 465", "port = 65536")

    def test_a_percentage_written_as_a_whole_number_is_refused(self):
        """15 means 1500%, and would inflate every estimate on the report."""
        with self.assertRaisesRegex(
            ValueError, "economics: default_buyer_premium cannot exceed 1"
        ):
            self._load_variant("default_buyer_premium = 0.15", "default_buyer_premium = 15")

    def test_a_misspelled_acquisition_mode_is_refused_at_load_time(self):
        """A typo used to degrade silently to manual mode and never explain itself."""
        with self.assertRaisesRegex(
            ValueError, "mode must be one of: manual, authorized_http"
        ):
            self._load_variant('mode = "authorized_http"', 'mode = "authorised_http"')

    def test_an_unknown_run_mode_is_refused_at_load_time(self):
        with self.assertRaisesRegex(
            ValueError, "run_mode must be one of: production, development"
        ):
            self._load_variant(
                'mode = "authorized_http"',
                'mode = "authorized_http"\nrun_mode = "staging"',
            )

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
