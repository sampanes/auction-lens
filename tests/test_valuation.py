"""Fanning a listing out to configured valuation sources."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from auction_lens.config import ValuationSourceConfig
from auction_lens.models import ValuationObservation
from auction_lens.valuation import ValuationEngine, create_adapter
from auction_lens.valuation.aggregation import combine_into_bands
from auction_lens.valuation.http_json import HttpJsonAdapter
from auction_lens.valuation.json_path import read_path
from auction_lens.valuation.templates import fill_template
from support import SOUNDBAR, FakeResponse, RecordingOpener, example_config, example_listings
from support import temporary_directory

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
        valuation = replace(self.config.valuation, sources=(*self.config.valuation.sources, broken))
        summary = ValuationEngine(valuation).value(self.listing)
        self.assertTrue(summary.bands)
        self.assertIn("broken: ValueError", summary.errors[0])

    def test_an_unknown_adapter_names_the_built_in_choices(self):
        source = ValuationSourceConfig(source_id="mystery", adapter="mystery")
        with self.assertRaisesRegex(ValueError, "built-ins: http_json, reference, xml_catalog"):
            create_adapter(source)


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

    def test_a_listing_without_brand_or_model_falls_back_to_its_title(self):
        listing = replace(example_listings()[SOUNDBAR], brand="", model="", title="Odd Lot")
        self.assertEqual(fill_template("{query}", listing), "Odd+Lot")


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

    def test_a_non_https_endpoint_is_refused_before_any_request(self):
        opener = RecordingOpener(FakeResponse(API_BODY))
        with temporary_directory() as directory:
            source = self._source(directory, endpoint="http://example.invalid/value?q={query}")
            with self.assertRaisesRegex(ValueError, "public HTTPS"):
                HttpJsonAdapter(source, opener=opener).collect(self.listing)
        self.assertEqual(opener.request_count, 0)

    def test_the_per_run_request_budget_is_enforced(self):
        opener = RecordingOpener(FakeResponse(API_BODY))
        with temporary_directory() as directory:
            adapter = HttpJsonAdapter(self._source(directory, max_requests_per_run=2), opener=opener)
            adapter.collect(self.listing)
            adapter.collect(replace(self.listing, model="SB22"))
            with self.assertRaisesRegex(RuntimeError, "max_requests_per_run"):
                adapter.collect(replace(self.listing, model="SB23"))
        self.assertEqual(opener.request_count, 2)

    def _source(self, directory, **overrides) -> ValuationSourceConfig:
        settings = {
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


def _observation(**overrides) -> ValuationObservation:
    values = {
        "source_id": "test",
        "basis": "used_sold",
        "low": Decimal("50"),
        "typical": Decimal("100"),
        "high": Decimal("150"),
    }
    values.update(overrides)
    for name in ("low", "typical", "high", "confidence"):
        if name in values and values[name] is not None:
            values[name] = Decimal(str(values[name]))
    return ValuationObservation(**values)


if __name__ == "__main__":
    unittest.main()
