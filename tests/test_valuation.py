"""Fanning a listing out to configured valuation sources."""

from __future__ import annotations

import json
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from auction_lens.config.pricing import ValuationSourceConfig
from auction_lens.http_safety import PublicHttpsRedirectHandler
from auction_lens.matching.evaluate import evaluate
from auction_lens.pricing.closing_history import (
    BASIS,
    SETTLED_BASIS,
    ClosingHistoryAdapter,
    _read_only,
    likely_close,
)
from auction_lens.pricing.http_json import HttpJsonAdapter, read_path
from auction_lens.pricing.model import (
    ValuationBand,
    ValuationObservation,
    ValuationSummary,
)
from auction_lens.pricing.reference import ReferenceAdapter
from auction_lens.pricing.sources import fill_template
from auction_lens.pricing.value import (
    BUILTIN_ADAPTERS,
    ValuationEngine,
    combine_into_bands,
    create_adapter,
)
from auction_lens.reports.findings import build_report
from auction_lens.reports.html import render_html
from auction_lens.reports.text import render_text
from auction_lens.reports.webhook import build_message
from support import (
    REPORT_ZONE,
    SOUNDBAR,
    FakeResponse,
    RecordingOpener,
    example_config,
    example_listings,
    shortly_before_close,
    temporary_directory,
)

API_BODY = b'{"results":[{"range":{"low":"90","mid":"110","high":"130"},"sales":7}]}'


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listing = example_listings()[SOUNDBAR]

    def test_catalog_and_reference_sources_fan_out_from_configuration(self):
        summary = ValuationEngine(self.config.valuation).value(self.listing)
        self.assertEqual({band.basis for band in summary.bands}, {"new_street", "used_sold"})
        used = next(band for band in summary.bands if band.basis == "used_sold")
        self.assertEqual(used.typical, Decimal("68.00"))
        self.assertEqual(used.sample_size, 8)
        self.assertEqual(
            {link.source_id for link in summary.research_links}, {"general-sold-research"}
        )

    def test_a_category_specific_source_is_skipped_for_other_categories(self):
        summary = ValuationEngine(self.config.valuation).value(self.listing)
        self.assertNotIn(
            "music-gear-research", {link.source_id for link in summary.research_links}
        )

    def test_one_failing_source_does_not_erase_the_others(self):
        broken = ValuationSourceConfig(source_id="broken", adapter="reference", settings={})
        sources = (*self.config.valuation.sources, broken)
        valuation = replace(self.config.valuation, sources=sources)
        with self.assertLogs("auction_lens.pricing.value", level="WARNING"):
            summary = ValuationEngine(valuation).value(self.listing)
        self.assertTrue(summary.bands)
        self.assertEqual(summary.errors[0], "broken: unavailable (ValueError)")

    def test_adapter_details_stay_local_instead_of_reaching_reports(self):
        private_detail = r"C:\private-data\valuation.json"
        source = ValuationSourceConfig(source_id="broken", adapter="reference")

        class BrokenAdapter:
            def collect(self, listing):
                raise RuntimeError(f"could not read {private_detail}")

        with patch(
            "auction_lens.pricing.value.create_adapter", return_value=BrokenAdapter()
        ):
            engine = ValuationEngine(replace(self.config.valuation, sources=(source,)))
        with self.assertLogs("auction_lens.pricing.value", level="WARNING") as logs:
            summary = engine.value(self.listing)

        self.assertEqual(summary.errors, ("broken: unavailable (RuntimeError)",))
        self.assertNotIn(private_detail, "\n".join(logs.output))

        candidate = replace(
            self.configured_candidate(),
            valuation=summary,
        )
        rendered = (
            render_text(build_report([candidate], REPORT_ZONE)),
            render_html(build_report([candidate], REPORT_ZONE)),
        )
        for report in rendered:
            self.assertNotIn(private_detail, report)
            self.assertIn("broken: unavailable (RuntimeError)", report)
        webhook = json.dumps(build_message(
            build_report([candidate], REPORT_ZONE),
            self.config.webhook,
        ))
        self.assertNotIn(private_detail, webhook)

    def test_an_unknown_adapter_names_the_built_in_choices(self):
        """Every built-in, read from the registry so a new one cannot be missed.

        Spelling the list out here made adding an adapter fail this test, which
        says nothing about whether the message is useful.
        """
        source = ValuationSourceConfig(source_id="mystery", adapter="mystery")
        with self.assertRaises(ValueError) as refused:
            create_adapter(source)
        said = str(refused.exception)
        self.assertIn("unknown valuation adapter 'mystery'", said)
        for name in BUILTIN_ADAPTERS:
            self.assertIn(name, said)

    def configured_candidate(self):
        """One ordinary candidate to carry an engine result into both reports."""
        return evaluate(self.listing, self.config, now=shortly_before_close())[0]


class AggregationTests(unittest.TestCase):
    def test_confident_larger_sample_decides_the_typical_value(self):
        bands = combine_into_bands(
            [
                _observation(typical="100", sample_size=1, confidence="0.2"),
                _observation(typical="200", sample_size=10, confidence="0.9"),
            ]
        )
        self.assertEqual(bands[0].typical, Decimal("200"))
        self.assertEqual(bands[0].sample_size, 11)
        self.assertEqual(bands[0].source_count, 1)

    def test_observations_of_different_bases_stay_separate(self):
        bands = combine_into_bands(
            [_observation(basis="used_sold"), _observation(basis="new_street")]
        )
        self.assertEqual([band.basis for band in bands], ["new_street", "used_sold"])


class TemplateTests(unittest.TestCase):
    def test_placeholders_are_filled_and_url_encoded(self):
        listing = replace(example_listings()[SOUNDBAR], brand="Ex Co", model="SB 21")
        url = fill_template("https://example.invalid/s?q={query}&b={brand}", listing)
        self.assertEqual(url, "https://example.invalid/s?q=Ex+Co+SB+21&b=Ex+Co")

    def test_incomplete_structured_identity_falls_back_to_the_whole_title(self):
        original = example_listings()[SOUNDBAR]
        for brand, model in (("", ""), ("Example", ""), ("", "T100")):
            with self.subTest(brand=brand, model=model):
                listing = replace(
                    original, brand=brand, model=model, title="Example T100 Speaker"
                )
                self.assertEqual(fill_template("{query}", listing), "Example+T100+Speaker")


class JsonPathTests(unittest.TestCase):
    def test_object_keys_and_list_indexes_are_both_supported(self):
        payload = {"results": [{"price": {"value": 12}}]}
        self.assertEqual(read_path(payload, "results.0.price.value"), 12)

    def test_an_empty_path_means_the_whole_payload(self):
        self.assertEqual(read_path({"a": 1}, ""), {"a": 1})


class HttpJsonAdapterTests(unittest.TestCase):
    def setUp(self):
        self.listing = example_listings()[SOUNDBAR]

    def test_declared_fields_are_mapped_and_the_response_is_cached(self):
        opener = RecordingOpener(FakeResponse(API_BODY))
        with temporary_directory() as directory:
            adapter = HttpJsonAdapter(self._source(directory), opener=opener)
            first = adapter.collect(self.listing)
            second = adapter.collect(self.listing)

        self.assertEqual(first, second)
        self.assertEqual(first.observations[0].typical, Decimal("110.00"))
        self.assertEqual(first.observations[0].sample_size, 7)
        self.assertEqual(first.observations[0].basis, "used_sold")
        self.assertEqual(opener.request_count, 1)

    @patch("auction_lens.pricing.http_json.public_https_opener")
    def test_the_default_path_uses_the_redirect_safe_opener(self, opener_factory):
        opener = RecordingOpener(FakeResponse(API_BODY))
        opener_factory.return_value = opener
        with temporary_directory() as directory:
            HttpJsonAdapter(self._source(directory)).collect(self.listing)
        opener_factory.assert_called_once_with()
        self.assertEqual(opener.request_count, 1)

    def test_an_api_header_cannot_follow_a_cross_origin_redirect(self):
        request = Request(
            "https://api.example.invalid/value",
            headers={"Authorization": "synthetic-test-value"},
        )
        with self.assertRaisesRegex(RuntimeError, "different origin"):
            PublicHttpsRedirectHandler().redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://elsewhere.example.invalid/value",
            )

    def test_a_non_https_endpoint_is_refused_before_any_request(self):
        opener = RecordingOpener(FakeResponse(API_BODY))
        with temporary_directory() as directory:
            source = self._source(directory, endpoint="http://example.invalid/value?q={query}")
            with self.assertRaisesRegex(ValueError, "public HTTPS"):
                HttpJsonAdapter(source, opener=opener).collect(self.listing)
        self.assertEqual(opener.request_count, 0)

    def test_an_unconfirmed_source_is_never_queried(self):
        opener = RecordingOpener(FakeResponse(API_BODY))
        with temporary_directory() as directory:
            source = self._source(directory, authorization_confirmed=False)
            with self.assertRaisesRegex(ValueError, "authorization_confirmed = true"):
                HttpJsonAdapter(source, opener=opener).collect(self.listing)
        self.assertEqual(opener.request_count, 0)

    def test_the_per_run_request_budget_is_enforced(self):
        opener = RecordingOpener(FakeResponse(API_BODY))
        with temporary_directory() as directory:
            source = self._source(directory, max_requests_per_run=2)
            adapter = HttpJsonAdapter(source, opener=opener)
            adapter.collect(self.listing)
            adapter.collect(replace(self.listing, model="SB22"))
            with self.assertRaisesRegex(RuntimeError, "max_requests_per_run"):
                adapter.collect(replace(self.listing, model="SB23"))
        self.assertEqual(opener.request_count, 2)

    def test_a_fractional_sample_size_is_not_silently_truncated(self):
        body = (
            b'{"results":[{"range":{"low":"90","mid":"110","high":"130"},'
            b'"sales":1.5}]}'
        )
        opener = RecordingOpener(FakeResponse(body))
        with temporary_directory() as directory:
            with self.assertRaisesRegex(ValueError, "sample_size must be a whole number"):
                HttpJsonAdapter(self._source(directory), opener=opener).collect(self.listing)

    def _source(self, directory, **overrides) -> ValuationSourceConfig:
        settings = {
            "authorization_confirmed": True,
            "endpoint": "https://example.invalid/value?q={query}",
            "items_path": "results",
            "basis": "used_sold",
            "cache_dir": str(directory),
            "minimum_interval_seconds": 0,
            "fields": {
                "low": "range.low",
                "typical": "range.mid",
                "high": "range.high",
                "sample_size": "sales",
            },
        }
        settings.update(overrides)
        return ValuationSourceConfig(
            source_id="configurable-api", adapter="http_json", settings=settings
        )


class SourceSettingsTests(unittest.TestCase):
    """A source's own settings are configuration, held to the same standard.

    Each of these used to be accepted: a negative lifetime made every cached
    answer stale, a fractional budget was truncated, and an unreadable value
    failed somewhere deep in the standard library naming nothing.
    """

    def test_a_negative_cache_lifetime_is_refused(self):
        with self.assertRaisesRegex(ValueError, "cache_hours cannot be negative"):
            HttpJsonAdapter(_configured(cache_hours=-1))

    def test_a_spent_request_budget_is_a_configuration_error_not_a_runtime_one(self):
        with self.assertRaisesRegex(ValueError, "max_requests_per_run must be at least 1"):
            HttpJsonAdapter(_configured(max_requests_per_run=0))

    def test_a_fractional_timeout_is_refused_rather_than_truncated(self):
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be a whole number"):
            HttpJsonAdapter(_configured(timeout_seconds=20.5))

    def test_an_unreadable_setting_names_the_source_and_the_key(self):
        with self.assertRaisesRegex(
            ValueError, r"valuation\.sources\.pricing-api\.cache_hours must be a number"
        ):
            HttpJsonAdapter(_configured(cache_hours="soon"))

    def test_a_missing_required_setting_names_the_key_to_add(self):
        source = ValuationSourceConfig(source_id="sold-research", adapter="reference")
        with self.assertRaisesRegex(
            ValueError, r"valuation\.sources\.sold-research\.url_template is required"
        ):
            ReferenceAdapter(source).collect(example_listings()[SOUNDBAR])


class ClosingHistoryTests(unittest.TestCase):
    """Pricing a lot from what the same product really fetched here before.

    The point of the source is that stated retail is the seller's own number.
    So these tests care about two things above all: that a price only counts
    when it was read late enough to be a close, and that a lot can never be
    its own evidence.
    """

    NOW = "2026-01-10T18:00:00+00:00"

    def _database(self, directory, rows):
        """Build a throwaway history. Each row is one lot and one reading."""
        location = Path(directory) / "history.sqlite3"
        connection = sqlite3.connect(location)
        connection.execute(
            "CREATE TABLE listings (source TEXT, listing_id TEXT, title TEXT, "
            "ends_at TEXT, estimated_retail TEXT)"
        )
        connection.execute(
            "CREATE TABLE price_history (source TEXT, listing_id TEXT, "
            "observed_at TEXT, current_bid TEXT)"
        )
        for listing_id, title, ends_at, observed_at, bid in rows:
            connection.execute(
                "INSERT INTO listings (source, listing_id, title, ends_at) "
                "VALUES ('nellis', ?, ?, ?)",
                (listing_id, title, ends_at),
            )
            connection.execute(
                "INSERT INTO price_history VALUES ('nellis', ?, ?, ?)",
                (listing_id, observed_at, bid),
            )
        connection.commit()
        connection.close()
        return location

    def _adapter(self, location, **settings):
        settings.setdefault("path", str(location))
        return ClosingHistoryAdapter(
            ValuationSourceConfig(
                source_id="closing-history",
                adapter="closing_history",
                settings=settings,
            )
        )

    def _closed(self, listing_id, title, bid, *, minutes_early=5):
        """One lot that closed, read `minutes_early` before it ended."""
        ends = datetime.fromisoformat(self.NOW)
        seen = ends - timedelta(minutes=minutes_early)
        return (listing_id, title, ends.isoformat(), seen.isoformat(), bid)

    def _subject(self, title, listing_id="999"):
        return replace(
            example_listings()[SOUNDBAR], listing_id=listing_id, title=title
        )

    def test_it_reports_the_observed_spread(self):
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
                self._closed("2", "Northvale Patio Heater 48in", "60.00"),
                self._closed("3", "Northvale Patio Heater 48in", "50.00"),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        self.assertEqual(len(found.observations), 1)
        priced = found.observations[0]
        self.assertEqual(priced.low, Decimal("40.00"))
        self.assertEqual(priced.typical, Decimal("50.00"))
        self.assertEqual(priced.high, Decimal("60.00"))
        self.assertEqual(priced.sample_size, 3)

    def test_an_even_number_of_closes_reports_the_midpoint(self):
        """median_low would report the lower close as the typical price."""
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "80.00"),
                self._closed("2", "Northvale Patio Heater 48in", "300.00"),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        self.assertEqual(found.observations[0].typical, Decimal("190.00"))

    def test_a_reading_taken_too_early_is_not_a_close(self):
        """It is a lower bound that happens to be the last one recorded."""
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
                self._closed("2", "Northvale Patio Heater 48in", "60.00",
                             minutes_early=240),
                self._closed("3", "Northvale Patio Heater 48in", "50.00",
                             minutes_early=240),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        self.assertEqual(found.observations, ())

    def test_a_lot_is_never_its_own_comparable(self):
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
                self._closed("2", "Northvale Patio Heater 48in", "60.00"),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in", listing_id="2")
            )
        self.assertEqual(found.observations, ())

    def test_too_few_closes_says_nothing_rather_than_guessing(self):
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        self.assertEqual(found.observations, ())

    def test_a_lot_nobody_bid_on_says_nothing_about_the_product(self):
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "0.00"),
                self._closed("2", "Northvale Patio Heater 48in", "0.00"),
                self._closed("3", "Northvale Patio Heater 48in", "45.00"),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        self.assertEqual(found.observations, ())

    def test_a_missing_database_is_silence_not_a_crash(self):
        with temporary_directory() as directory:
            absent = Path(directory) / "nothing-here.sqlite3"
            found = self._adapter(absent).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        self.assertEqual(found.observations, ())

    def test_the_warehouse_shouting_does_not_split_a_product(self):
        """A marker describes this copy or its collection, not the product."""
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "***FACTORY SEALED*** Northvale Heater 48in",
                             "40.00"),
                self._closed("2", "Northvale Heater - 48in", "60.00"),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Heater, 48in")
            )
        self.assertEqual(found.observations[0].sample_size, 2)

    def test_a_different_model_is_a_different_product(self):
        """The digits in a title are what separate one model from the next."""
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Heater 48in", "40.00"),
                self._closed("2", "Northvale Heater 60in", "60.00"),
                self._closed("3", "Northvale Heater 72in", "80.00"),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Heater 48in")
            )
        self.assertEqual(found.observations, ())

    # ---- a product with no closes of its own gets no answer -----------------

    def _staged(self, directory, lots, *, closes=()):
        """A history of whole lots: (title, retail, final bid, early readings).

        Each early reading is (hours before the close, bid). The final bid is
        read five minutes before the close, so every lot here really closed.
        `closes` adds single-reading lots in the shape `_closed` makes.
        """
        location = self._database(directory, list(closes))
        connection = sqlite3.connect(location)
        ends = datetime.fromisoformat(self.NOW)
        for number, (title, retail, final, early) in enumerate(lots, start=100):
            listing_id = str(number)
            connection.execute(
                "INSERT INTO listings VALUES ('nellis', ?, ?, ?, ?)",
                (listing_id, title, ends.isoformat(), retail),
            )
            readings = [(hours * 60, bid) for hours, bid in early] + [(5, final)]
            for minutes, bid in readings:
                connection.execute(
                    "INSERT INTO price_history VALUES ('nellis', ?, ?, ?)",
                    (listing_id, (ends - timedelta(minutes=minutes)).isoformat(), bid),
                )
        connection.commit()
        connection.close()
        return location

    def _forty_lots(self):
        """Lots of retail $100 that sat at $1 eight hours out and closed at
        $10, $11, ... so their closes are 10%, 11%, ... of retail."""
        return [
            (f"Unrelated Gadget {number}", "100", f"{10 + number}.00", ((8, "1.00"),))
            for number in range(40)
        ]

    def _staged_subject(self):
        ends = datetime.fromisoformat(self.NOW) + timedelta(days=1)
        return replace(
            self._subject("Northvale Patio Heater 48in"),
            current_bid=Decimal("1.00"),
            estimated_retail=Decimal("200"),
            ends_at=ends,
            observed_at=ends - timedelta(hours=8),
        )

    def test_a_product_with_no_closes_of_its_own_is_not_priced(self):
        """Forty lots that closed at a known share of retail still say nothing.

        Their closes were once scaled to this lot's stated retail and reported
        as the middle half of lots at the same stage. What that produced was
        the retail figure times one constant per stage, so it read as evidence
        while carrying none -- and stated retail is the seller's own number.
        Measured over 16,965 closes, the share of retail a lot ends at is a
        curve decaying from zero, median 16.5% and mean 19.7%, with only 2.1%
        of lots landing within a point of that mean. No multiple of retail
        describes a lot, so the honest answer is none, and the card says so.
        """
        with temporary_directory() as directory:
            location = self._staged(directory, self._forty_lots())
            found = self._adapter(location).collect(self._staged_subject())
        self.assertEqual(found.observations, ())

    def test_the_product_s_own_closes_are_still_read_past_those_lots(self):
        """Removing the fallback must not have cost the evidence it stood in for."""
        with temporary_directory() as directory:
            location = self._staged(directory, self._forty_lots(), closes=[
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
                self._closed("2", "Northvale Patio Heater 48in", "60.00"),
            ])
            found = self._adapter(location).collect(self._staged_subject())
        self.assertEqual([item.basis for item in found.observations], [BASIS])

    # ---- a recorded sale outranks a reading taken before the close ----------

    def _with_sales(self, location, sales):
        """Add settled closes to a history. Each sale is (listing_id, final bid)."""
        connection = sqlite3.connect(location)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS settled_closes (source TEXT, "
            "listing_id TEXT, final_bid TEXT, bid_count INTEGER, closed_at TEXT, "
            "scheduled_close TEXT, extended INTEGER, settled_at TEXT)"
        )
        for listing_id, final_bid in sales:
            connection.execute(
                "INSERT INTO settled_closes VALUES ('nellis', ?, ?, 4, NULL, NULL, 0, "
                "'2026-10-17T23:45:00+00:00')",
                (listing_id, final_bid),
            )
        connection.commit()
        connection.close()
        return location

    def test_a_recorded_sale_is_preferred_to_a_reading_before_the_close(self):
        """Not pooled with it: only one of the two is a price somebody paid.

        A reading taken before the end is a floor, so averaging it with a sale
        would pull the better answer down by exactly the error that made
        reading-based prices worth replacing in the first place.
        """
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
                self._closed("2", "Northvale Patio Heater 48in", "60.00"),
            ])
            self._with_sales(location, [("1", "90.00"), ("2", "110.00")])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        priced = found.observations[0]
        self.assertEqual(priced.basis, SETTLED_BASIS)
        self.assertEqual(priced.typical, Decimal("100.00"))
        self.assertIn("recorded sales", priced.notes)

    def test_too_few_sales_falls_back_to_the_readings_rather_than_mixing_them(self):
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
                self._closed("2", "Northvale Patio Heater 48in", "60.00"),
            ])
            self._with_sales(location, [("1", "90.00")])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        priced = found.observations[0]
        self.assertEqual(priced.basis, BASIS)
        self.assertEqual(priced.typical, Decimal("50.00"))

    def test_a_history_written_before_settling_existed_is_not_a_failure(self):
        """The table is absent on every database until a settling pass runs."""
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
                self._closed("2", "Northvale Patio Heater 48in", "60.00"),
            ])
            found = self._adapter(location).collect(
                self._subject("Northvale Patio Heater 48in")
            )
        self.assertEqual(found.observations[0].basis, BASIS)

    def test_a_lot_is_never_its_own_comparable_sale(self):
        """Its own result must not price it, however many times it is read."""
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Patio Heater 48in", "40.00"),
                self._closed("3", "Northvale Patio Heater 48in", "60.00"),
            ])
            self._with_sales(
                location,
                [("1", "90.00"), ("3", "150.00"), ("2", "999.00")],
            )
            subject = replace(self._subject("Northvale Patio Heater 48in"), listing_id="2")
            found = self._adapter(location).collect(subject)
        priced = found.observations[0]
        self.assertEqual(priced.basis, SETTLED_BASIS)
        self.assertEqual(priced.sample_size, 2)
        self.assertEqual(priced.typical, Decimal("120.00"))
        self.assertEqual(priced.high, Decimal("150.00"))

    def test_a_likely_close_is_never_below_the_bid_already_placed(self):
        summary = ValuationSummary(bands=(
            ValuationBand(basis=BASIS, low=Decimal("20"), typical=Decimal("30"),
                          high=Decimal("50"), source_count=1, sample_size=3),
        ))
        likely = likely_close(summary, Decimal("35"))
        self.assertEqual(
            (likely.low, likely.typical, likely.high),
            (Decimal("35"), Decimal("35"), Decimal("50")),
        )

    def test_evidence_of_another_kind_is_not_a_likely_close(self):
        summary = ValuationSummary(bands=(
            ValuationBand(basis="used_sold", low=Decimal("20"), typical=Decimal("30"),
                          high=Decimal("50"), source_count=1, sample_size=3),
        ))
        self.assertIsNone(likely_close(summary, Decimal("1")))

    def test_the_adapter_is_reachable_by_its_configured_name(self):
        built = create_adapter(
            ValuationSourceConfig(
                source_id="closing-history",
                adapter="closing_history",
                settings={"path": "unused"},
            )
        )
        self.assertIsInstance(built, ClosingHistoryAdapter)

    def test_the_history_is_opened_read_only(self):
        """A valuation source must not be able to alter the record it reads."""
        with temporary_directory() as directory:
            location = self._database(directory, [
                self._closed("1", "Northvale Heater 48in", "40.00"),
                self._closed("2", "Northvale Heater 48in", "60.00"),
            ])
            adapter = self._adapter(location)
            adapter.collect(self._subject("Northvale Heater 48in"))
            with _read_only(location) as connection:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("DELETE FROM listings")


def _configured(**settings) -> ValuationSourceConfig:
    """An otherwise valid HTTP source, so each test varies one setting alone."""
    settings.setdefault("endpoint", "https://example.invalid/value?q={query}")
    settings.setdefault("fields", {"typical": "range.mid"})
    return ValuationSourceConfig(
        source_id="pricing-api", adapter="http_json", settings=settings
    )


def _observation(**overrides) -> ValuationObservation:
    values = {"source_id": "test", "basis": "used_sold", "typical": Decimal("100")}
    values.update(overrides)
    # Keep an overridden typical inside its band unless the caller set the edges.
    typical = Decimal(str(values["typical"]))
    values.setdefault("low", typical / 2)
    values.setdefault("high", typical * 2)
    for name in ("low", "typical", "high", "confidence"):
        if name in values and values[name] is not None:
            values[name] = Decimal(str(values[name]))
    return ValuationObservation(**values)


if __name__ == "__main__":
    unittest.main()
