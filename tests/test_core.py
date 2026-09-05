from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from auction_lens.config import load_config
from auction_lens.config import ConditionPolicy, InterestRule, ValuationSourceConfig
from auction_lens.ingest import load_listings
from auction_lens.http_source import fetch_authorized_page
from auction_lens.models import LogisticsDecision
from auction_lens.reporting import load_env_file, render_text, send_email
from auction_lens.scoring import estimate_total_cost, evaluate
from auction_lens.storage import ObservationStore
from auction_lens.valuation import ValuationEngine
from auction_lens.valuation.http_json import HttpJsonAdapter


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "providers" / "nellis.example.toml"
FIXTURE = ROOT / "fixtures" / "synthetic" / "listings.json"
NELLIS_BROWSE_FIXTURE = ROOT / "fixtures" / "nellis" / "browse-shell.html"


class AuctionLensTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG)
        self.listings = load_listings(FIXTURE)

    def test_total_cost_uses_listing_premium(self):
        self.assertEqual(estimate_total_cost(self.listings[0], self.config), Decimal("20.70"))

    def test_wanted_and_anomaly_rules(self):
        candidates = evaluate(self.listings[0], self.config)
        self.assertEqual({item.category for item in candidates}, {"wanted", "anomaly"})

    def test_reusable_condition_profile_is_loaded(self):
        soundbar = next(rule for rule in self.config.interests if rule.name == "soundbar")
        self.assertEqual(soundbar.condition_profile, "ready_to_use")
        self.assertIn("not functional", soundbar.condition.reject)
        self.assertEqual(soundbar.condition.penalties["untested"], 22)

    def test_store_detects_new_and_price_stability(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ObservationStore(Path(directory) / "observations.sqlite3")
            store.initialize()
            first = store.observe(self.listings[0])
            second = store.observe(self.listings[0])
        self.assertTrue(first.is_new)
        self.assertFalse(second.is_new)
        self.assertFalse(second.price_changed)

    def test_store_detects_price_change(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ObservationStore(Path(directory) / "observations.sqlite3")
            store.initialize()
            store.observe(self.listings[0])
            changed_listing = replace(
                self.listings[0],
                current_bid=Decimal("19.00"),
                observed_at=self.listings[0].observed_at + timedelta(seconds=1),
            )
            changed = store.observe(changed_listing)
        self.assertTrue(changed.price_changed)
        self.assertEqual(changed.previous_bid, Decimal("18.00"))

    def test_logistics_decision_round_trips_in_private_database(self):
        decision = LogisticsDecision(
            status="feasible", added_cost=Decimal("12.50"), note="generic test plan"
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ObservationStore(Path(directory) / "observations.sqlite3")
            store.initialize()
            store.set_logistics_decision("example", "large-1", decision)
            loaded = store.get_logistics_decision("example", "large-1")
            store.clear_logistics_decision("example", "large-1")
            cleared = store.get_logistics_decision("example", "large-1")
        self.assertEqual(loaded, decision)
        self.assertIsNone(cleared)

    def test_rejected_condition_never_becomes_candidate(self):
        rejected = replace(self.listings[1], conditions=("not functional",))
        self.assertEqual(evaluate(rejected, self.config), [])

    def test_condition_policy_is_scoped_to_intended_use(self):
        salvage = InterestRule(
            name="square tubing stock",
            purpose="salvage",
            all_terms=("square", "tubing"),
            condition=ConditionPolicy(reject=frozenset()),
        )
        config = replace(self.config, interests=(salvage,))
        listing = replace(
            self.listings[0],
            title="Broken work stand with square steel tubing",
            estimated_retail=None,
            conditions=("not functional", "1 of 3 working"),
        )
        candidates = evaluate(listing, config)
        self.assertEqual([item.rule_name for item in candidates], ["square tubing stock"])
        self.assertIn("salvage interest", candidates[0].reasons[0])

    def test_loading_assistance_leaves_only_destination_handling_unresolved(self):
        listing = replace(
            self.listings[0],
            handling_weight_lb=Decimal("148"),
            package_dimensions_in=(Decimal("70"), Decimal("31"), Decimal("45")),
            loading_assistance=("forklift",),
        )
        candidate = next(item for item in evaluate(listing, self.config) if item.category == "wanted")
        self.assertEqual(candidate.logistics.status, "needs_plan")
        questions = " ".join(candidate.logistics.questions).lower()
        self.assertIn("fits the planned transport", questions)
        self.assertIn("destination unloading", questions)
        self.assertNotIn("origin loading", questions)
        self.assertIn("LOGISTICS CHECK", render_text([candidate]))

    def test_saved_logistics_decision_affects_cost_and_eligibility(self):
        listing = replace(self.listings[0], handling_weight_lb=Decimal("148"))
        feasible = LogisticsDecision(status="feasible", added_cost=Decimal("10"))
        candidate = next(
            item
            for item in evaluate(listing, self.config, logistics_decision=feasible)
            if item.category == "wanted"
        )
        self.assertEqual(candidate.total_cost, Decimal("30.70"))
        self.assertEqual(candidate.logistics.status, "feasible")

        too_expensive = replace(feasible, added_cost=Decimal("40"))
        self.assertEqual(
            evaluate(listing, self.config, logistics_decision=too_expensive), []
        )
        self.assertEqual(
            evaluate(
                listing,
                self.config,
                logistics_decision=LogisticsDecision(status="infeasible"),
            ),
            [],
        )

    def test_xml_catalog_and_reference_sources_fan_out_from_config(self):
        summary = ValuationEngine(self.config.valuation).value(self.listings[0])
        self.assertEqual({band.basis for band in summary.bands}, {"new_street", "used_sold"})
        used = next(band for band in summary.bands if band.basis == "used_sold")
        self.assertEqual(used.typical, Decimal("68.00"))
        self.assertEqual(used.sample_size, 8)
        self.assertEqual(
            {link.source_id for link in summary.research_links},
            {"general-sold-research"},
        )

    def test_http_json_adapter_is_declarative_and_cached(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"results":[{"range":{"low":"90","mid":"110","high":"130"},"sales":7}]}'

        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as directory:
            source = ValuationSourceConfig(
                source_id="configurable-api",
                adapter="http_json",
                settings={
                    "authorization_confirmed": True,
                    "endpoint": "https://example.invalid/value?q={query}",
                    "items_path": "results",
                    "basis": "used_sold",
                    "cache_dir": directory,
                    "fields": {
                        "low": "range.low",
                        "typical": "range.mid",
                        "high": "range.high",
                        "sample_size": "sales",
                    },
                },
            )
            adapter = HttpJsonAdapter(source, opener=opener)
            first = adapter.collect(self.listings[0])
            second = adapter.collect(self.listings[0])

        self.assertEqual(first, second)
        self.assertEqual(first.observations[0].typical, Decimal("110.00"))
        self.assertEqual(first.observations[0].sample_size, 7)
        self.assertEqual(len(calls), 1)

    def test_report_contains_actionable_cost(self):
        report = render_text(evaluate(self.listings[1], self.config))
        self.assertIn("estimated total $12.65", report)
        self.assertIn("Example Laser Level Kit", report)

    @patch("auction_lens.reporting.smtplib.SMTP_SSL")
    def test_email_uses_environment_without_exposing_credentials(self, smtp_ssl):
        candidates = evaluate(self.listings[1], self.config)
        environment = {
            "AUCTION_LENS_SMTP_HOST": "smtp.example.invalid",
            "AUCTION_LENS_SMTP_USERNAME": "user",
            "AUCTION_LENS_SMTP_PASSWORD": "secret",
            "AUCTION_LENS_EMAIL_FROM": "sender@example.invalid",
            "AUCTION_LENS_EMAIL_TO": "recipient@example.invalid",
        }
        with patch.dict("os.environ", environment, clear=False):
            send_email(candidates, self.config.email)
        smtp_ssl.assert_called_once_with("smtp.example.invalid", 465, timeout=30)
        smtp_ssl.return_value.__enter__.return_value.send_message.assert_called_once()

    @patch("auction_lens.reporting.smtplib.SMTP")
    @patch("auction_lens.reporting.smtplib.SMTP_SSL")
    def test_email_rejects_unknown_security_without_connecting(self, smtp_ssl, smtp):
        with self.assertRaisesRegex(ValueError, "email security must be"):
            send_email([], replace(self.config.email, security="starttlz"))
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()

        with tempfile.TemporaryDirectory() as directory:
            invalid_config = Path(directory) / "invalid-email-security.toml"
            invalid_config.write_text(
                CONFIG.read_text(encoding="utf-8").replace(
                    'security = "ssl"', 'security = "starttlz"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "email security must be"):
                load_config(invalid_config)

    def test_env_file_loads_values_without_overriding_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("NEW_SETTING=from-file\nEXISTING_SETTING=from-file\n", encoding="utf-8")
            with patch.dict("os.environ", {"EXISTING_SETTING": "from-process"}, clear=True):
                load_env_file(env_file)
                self.assertEqual(__import__("os").environ["NEW_SETTING"], "from-file")
                self.assertEqual(__import__("os").environ["EXISTING_SETTING"], "from-process")

    def test_redacted_nellis_browse_fixture_preserves_acquisition_boundaries(self):
        fixture = NELLIS_BROWSE_FIXTURE.read_text(encoding="utf-8")
        self.assertIn('action="/search"', fixture)
        self.assertIn('href="/browse/az"', fixture)
        self.assertIn("window.__remixContext", fixture)
        self.assertIn('"APP_PUBLIC_ALGOLIA_API_KEY": "REDACTED"', fixture)
        self.assertNotIn("@", fixture)

    def test_authorized_fetch_identifies_caches_and_rate_limits(self):
        class FakeResponse:
            status = 200
            headers = {"ETag": '"fixture-v1"'}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"<html>fixture</html>"

        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as directory:
            provider = replace(
                self.config.provider,
                enabled=True,
                acquisition_mode="authorized_http",
                url="https://example.invalid/public-listings",
                timezone="America/Phoenix",
                max_requests_per_day=2,
                minimum_interval_minutes=180,
                cache_file=str(Path(directory) / "response.html"),
                ledger_file=str(Path(directory) / "ledger.json"),
            )
            environment = {provider.user_agent_env: "AuctionLens test contact=test@example.invalid"}
            instant = datetime(2026, 9, 4, 16, tzinfo=timezone.utc)
            with patch.dict("os.environ", environment, clear=False):
                result = fetch_authorized_page(provider, now=instant, opener=opener)
                with self.assertRaisesRegex(RuntimeError, "minimum interval"):
                    fetch_authorized_page(provider, now=instant + timedelta(minutes=5), opener=opener)

            cached_body = result.cache_path.read_bytes()

        self.assertEqual(result.status, 200)
        self.assertEqual(cached_body, b"<html>fixture</html>")
        self.assertIn("test@example.invalid", requests[0][0].get_header("User-agent"))
        self.assertEqual(len(requests), 1)

    def test_development_mode_allows_repeated_but_throttled_requests(self):
        class FakeResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"development fixture"

        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as directory:
            provider = replace(
                self.config.provider,
                enabled=True,
                acquisition_mode="authorized_http",
                run_mode="development",
                development_minimum_interval_seconds=2,
                url="https://example.invalid/public-listings",
                timezone="America/Phoenix",
                cache_file=str(Path(directory) / "response.html"),
                ledger_file=str(Path(directory) / "ledger.json"),
            )
            instant = datetime(2026, 9, 5, 16, tzinfo=timezone.utc)
            environment = {provider.user_agent_env: "AuctionLens test contact=test@example.invalid"}
            with patch.dict("os.environ", environment, clear=False):
                fetch_authorized_page(provider, now=instant, opener=opener)
                with self.assertRaisesRegex(RuntimeError, "development minimum interval"):
                    fetch_authorized_page(provider, now=instant + timedelta(seconds=1), opener=opener)
                fetch_authorized_page(provider, now=instant + timedelta(seconds=2), opener=opener)
                fetch_authorized_page(provider, now=instant + timedelta(seconds=4), opener=opener)

        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
