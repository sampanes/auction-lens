"""A provider that can only ever be read, and what a free lot really costs.

Every record here is invented. A real Vine page carries the operator's own
delivery identity and the rows it produces are account-linked, so nothing
observed is ever copied into a fixture.
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from auction_lens import collect
from auction_lens.doctor import doctor
from auction_lens.matching.evaluate import estimate_total_cost, evaluate
from auction_lens.providers.registry import resolve_provider
from auction_lens.providers.vine import read as vine
from auction_lens.providers.vine.rota import next_window, resume_after
from auction_lens.providers.vine.swept import (
    MAIL_WINDOW_HOURS,
    describe_age,
    is_recent,
    last_sweep_date,
    moment_from,
    newest_moment,
    sweep_is_due,
)
from support import (
    EXAMPLE_CONFIG,
    SOUNDBAR,
    anomaly_clock,
    example_config,
    example_listings,
    temporary_directory,
)

ESCAPED_TITLE = "Synthetic bench lamp, 12&#34; arm &amp; clamp"
DECODED_TITLE = 'Synthetic bench lamp, 12" arm & clamp'

SYNTHETIC_RECORDS = (
    {
        "source": "vine",
        "asin": "B0SYNTH0001",
        "title": ESCAPED_TITLE,
        "image_url": "https://example.invalid/lamp.jpg",
        "queue": "encore",
        "search_term": "",
        "page": 1,
        "pages_available": 3,
        "seen_at": "2026-01-02T03:04:05+00:00",
        "etv": None,
    },
    {
        "source": "vine",
        "asin": "B0SYNTH0002",
        "title": "Synthetic cable organiser, 6 pack",
        "image_url": "",
        "queue": "last_chance",
        "page": 2,
        "pages_available": 2,
        "seen_at": "2026-01-02T03:05:05+00:00",
        "etv": "41.00",
    },
)


def record_file_text(records=SYNTHETIC_RECORDS) -> str:
    """One JSON object per line, exactly as the companion writes them."""
    return "".join(json.dumps(record) + "\n" for record in records)


def vine_config(directory):
    """A configuration naming the Vine provider and nothing else unusual."""
    path = directory / "vine.toml"
    path.write_text(
        EXAMPLE_CONFIG.read_text(encoding="utf-8").replace('id = "nellis"', 'id = "vine"', 1),
        encoding="utf-8",
    )
    return path


class VineReaderTests(unittest.TestCase):
    def test_a_tile_becomes_a_lot_that_was_never_bid_on(self):
        rows = vine.read_records(record_file_text(), source="vine")

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["listing_id"], "B0SYNTH0001")
        self.assertEqual(first["url"], "https://www.amazon.com/dp/B0SYNTH0001")
        self.assertEqual(first["current_bid"], "0")
        self.assertTrue(first["value_is_taxable_income"])
        self.assertEqual(first["conditions"], ("new",))
        self.assertEqual(first["photo_urls"], ("https://example.invalid/lamp.jpg",))

    def test_an_escaped_dimension_survives_as_the_character_a_rule_matches(self):
        rows = vine.read_records(record_file_text(), source="vine")

        self.assertEqual(rows[0]["title"], DECODED_TITLE)

    def test_a_declared_value_is_carried_and_a_missing_one_is_not_invented(self):
        rows = vine.read_records(record_file_text(), source="vine")

        self.assertIsNone(rows[0]["estimated_retail"])
        self.assertEqual(rows[1]["estimated_retail"], "41.00")

    def test_a_tile_with_no_image_states_no_photo_rather_than_an_empty_one(self):
        rows = vine.read_records(record_file_text(), source="vine")

        self.assertEqual(rows[1]["photo_urls"], ())

    def test_one_unreadable_line_does_not_lose_the_rest_of_the_day(self):
        text = record_file_text() + "{not json\n" + json.dumps({"title": "no id"}) + "\n"

        rows = vine.read_records(text, source="vine")

        self.assertEqual(
            [row["listing_id"] for row in rows], ["B0SYNTH0001", "B0SYNTH0002"]
        )

    def test_a_file_with_nothing_readable_is_refused_rather_than_reported_empty(self):
        with self.assertRaisesRegex(ValueError, "not a companion record file"):
            vine.read_records("<html>not records at all</html>", source="vine")

    def test_every_sighting_is_kept_because_deduplication_is_owned_elsewhere(self):
        text = record_file_text(SYNTHETIC_RECORDS + (SYNTHETIC_RECORDS[0],))

        rows = vine.read_records(text, source="vine")

        self.assertEqual(len(rows), 3)


class VineTitleAsciiTests(unittest.TestCase):
    """A Vine title has to survive being printed, mailed and posted.

    These listings are written by sellers worldwide and arrive carrying
    typography a Windows console cannot encode. A report is text, so one such
    character used to end the whole run with a charmap error and produce
    nothing at all -- on the unattended morning mail, a line in a log.
    """

    def folded(self, title):
        record = {
            "asin": "B0SYNTH0003",
            "title": title,
            "queue": "encore",
            "seen_at": "2026-01-01T00:00:00Z",
        }
        rows = vine.read_records(json.dumps(record) + "\n", source="vine")
        return rows[0]["title"]

    def test_a_title_a_console_cannot_encode_is_folded_rather_than_dropped(self):
        folded = self.folded("Acme Birdhouse\uff0c Cedar")

        self.assertEqual(folded, "Acme Birdhouse, Cedar")
        folded.encode("cp1252")

    def test_the_inch_mark_a_dimension_rule_looks_for_becomes_a_quote(self):
        folded = self.folded("Acme 24\u201d Picture Frame")

        self.assertEqual(folded, 'Acme 24" Picture Frame')

    def test_a_dash_between_words_stays_a_separator(self):
        self.assertEqual(
            self.folded("Acme Trivet \u2013 Two Pack"), "Acme Trivet - Two Pack"
        )
        self.assertEqual(
            self.folded("Acme 2\u2011Tier Bookend"), "Acme 2-Tier Bookend"
        )

    def test_a_dimension_written_with_a_multiplication_sign_reads_as_by(self):
        self.assertEqual(
            self.folded("Acme Sundial 8 inch \u00d7 8 mm"), "Acme Sundial 8 inch x 8 mm"
        )

    def test_a_fraction_keeps_its_slash_instead_of_running_the_digits_together(self):
        # NFKD turns a half into digits around a fraction slash, so a fold that
        # mapped before decomposing would read "1/2 inch" as "12 inch".
        self.assertEqual(self.folded("Acme \u00bd inch Doorstop"), "Acme 1/2 inch Doorstop")

    def test_a_direction_mark_leaves_nothing_behind(self):
        self.assertEqual(self.folded("Acme Shoehorn\u200e"), "Acme Shoehorn")

    def test_an_escaped_entity_is_decoded_before_it_is_folded(self):
        # The fold has to run on what the entity meant, not on its spelling.
        self.assertEqual(
            self.folded("Acme 24&#8221; Picture Frame"), 'Acme 24" Picture Frame'
        )

    def test_an_ordinary_title_is_returned_exactly_as_it_was(self):
        plain = "Acme 16 Pack Napkin Rings, Brushed Finish"

        self.assertEqual(self.folded(plain), plain)


class VineRefusesToLookTests(unittest.TestCase):
    def test_discovery_refuses_and_says_what_to_run_instead(self):
        adapter = resolve_provider("vine")
        config = example_config()

        for refusing in (adapter.discover_searches, adapter.check_discovery_ready):
            with self.subTest(refusal=refusing.__name__):
                with self.assertRaisesRegex(ValueError, "cannot discover lots"):
                    refusing(config.provider, config.acquisition, ["anything"])

    def test_reading_records_never_looks_at_a_saved_html_page(self):
        with temporary_directory() as directory:
            (directory / "page.html").write_text("<html></html>", encoding="utf-8")
            (directory / "queue-2026-01-02.ndjson").write_text(
                record_file_text(), encoding="utf-8"
            )
            output = directory / "listings.json"
            args = argparse.Namespace(
                config=str(vine_config(directory)),
                input=str(directory),
                output=str(output),
            )

            with redirect_stdout(io.StringIO()):
                self.assertEqual(collect.pull(args), 0)

            written = json.loads(output.read_text(encoding="utf-8"))["listings"]
            self.assertEqual(
                sorted(row["listing_id"] for row in written),
                ["B0SYNTH0001", "B0SYNTH0002"],
            )


class ReceivedLotCostTests(unittest.TestCase):
    def setUp(self):
        config = example_config()
        self.economics = replace(
            config.economics,
            default_buyer_premium=Decimal("0"),
            sales_tax_rate=Decimal("0"),
            processing_fee=Decimal("0"),
            income_tax_rate=Decimal("0.25"),
        )
        self.received = replace(
            example_listings()[SOUNDBAR],
            current_bid=Decimal("0"),
            buyer_premium_rate=Decimal("0"),
            estimated_retail=Decimal("80.00"),
            value_is_taxable_income=True,
        )

    def test_a_lot_nobody_bid_on_is_not_reported_as_free(self):
        self.assertEqual(
            estimate_total_cost(self.received, self.economics), Decimal("20.00")
        )

    def test_an_ordinary_auction_lot_is_untouched_by_the_income_rate(self):
        bought = replace(self.received, value_is_taxable_income=False)

        self.assertEqual(estimate_total_cost(bought, self.economics), Decimal("0.00"))

    def test_a_lot_declaring_no_value_states_no_tax_rather_than_inventing_one(self):
        unvalued = replace(self.received, estimated_retail=None)

        self.assertEqual(estimate_total_cost(unvalued, self.economics), Decimal("0.00"))

    def test_an_unset_rate_leaves_every_cost_exactly_as_it_was(self):
        unrated = replace(self.economics, income_tax_rate=Decimal("0"))

        self.assertEqual(estimate_total_cost(self.received, unrated), Decimal("0.00"))


class ReceivedLotIsNoBargainTests(unittest.TestCase):
    """The same lot twice, differing only in how it was come by.

    Written as a pair because the interesting claim is not that a received lot
    is refused the bargain path -- a cost bar could do that by accident -- but
    that this exact lot would otherwise have taken it.
    """

    def setUp(self):
        config = example_config()
        self.config = replace(
            config,
            economics=replace(config.economics, income_tax_rate=Decimal("0.10")),
        )
        # Costs 10 against a declared 100: inside every configured bar, and a
        # tenth of stated value is well under the anomaly ceiling.
        self.lot = replace(
            example_listings()[SOUNDBAR],
            current_bid=Decimal("0"),
            buyer_premium_rate=Decimal("0"),
            estimated_retail=Decimal("100.00"),
            value_is_taxable_income=True,
        )

    def _categories(self, listing):
        # The clock is given so that a withheld bargain claim here can only
        # mean the Vine reason, never that the fixture closes far away.
        found = evaluate(listing, self.config, now=anomaly_clock())
        return {candidate.category for candidate in found}

    def test_bought_at_that_price_the_same_lot_is_a_bargain(self):
        bought = replace(self.lot, value_is_taxable_income=False, current_bid=Decimal("10"))

        self.assertIn("anomaly", self._categories(bought))

    def test_received_instead_it_is_not(self):
        self.assertNotIn("anomaly", self._categories(self.lot))

    def test_but_the_want_that_asked_for_it_still_matches(self):
        # Withholding the bargain claim is not the same as hiding the lot from
        # somebody who asked for one.
        self.assertIn("wanted", self._categories(self.lot))


class ReceivedFlagSurvivesTheFileTests(unittest.TestCase):
    def test_the_flag_reads_from_json_and_from_a_csv_cell_alike(self):
        from auction_lens.listings.model import Listing

        base = {
            "source": "vine",
            "listing_id": "B0SYNTH0003",
            "title": "Synthetic desk mat",
            "url": "https://example.invalid/dp/B0SYNTH0003",
            "current_bid": "0",
        }

        self.assertFalse(Listing.from_mapping(base).value_is_taxable_income)
        self.assertTrue(
            Listing.from_mapping({**base, "value_is_taxable_income": True})
            .value_is_taxable_income
        )
        self.assertTrue(
            Listing.from_mapping({**base, "value_is_taxable_income": "true"})
            .value_is_taxable_income
        )
        self.assertFalse(
            Listing.from_mapping({**base, "value_is_taxable_income": "false"})
            .value_is_taxable_income
        )


class RotaResumesWhereItWasSpentTests(unittest.TestCase):
    """A sweep hands back the terms it was lent and never searched.

    Advancing by the width of the window counted searches that did not happen.
    Amazon throttles a sweep after a term or two, so most of each window went
    unsearched while the rota stepped over it -- and because the window width
    and the list length shared a factor, the rota could only ever begin at half
    the positions in the list. Those other terms were reachable only by a sweep
    that survived two searches, which is the thing that keeps failing.
    """

    TOTAL = 46
    SERVED = 12

    def test_a_sweep_that_spent_everything_resumes_past_its_whole_window(self):
        self.assertEqual(resume_after(0, self.SERVED, self.SERVED, self.TOTAL), 12)

    def test_a_sweep_cut_short_hands_the_rest_back(self):
        self.assertEqual(resume_after(0, self.SERVED, 2, self.TOTAL), 2)

    def test_a_sweep_that_searched_nothing_still_steps_over_one_term(self):
        """Otherwise a page that never renders is served again for ever."""
        self.assertEqual(resume_after(5, self.SERVED, 0, self.TOTAL), 6)

    def test_it_cannot_claim_more_than_it_was_lent(self):
        self.assertEqual(resume_after(0, 3, 99, self.TOTAL), 3)

    def test_it_wraps_round_the_end_of_the_list(self):
        self.assertEqual(resume_after(44, self.SERVED, 4, self.TOTAL), 2)

    def test_an_empty_list_is_not_an_error(self):
        self.assertEqual(resume_after(0, 0, 0, 0), 0)

    def test_every_term_is_reachable_even_when_no_sweep_ever_succeeds(self):
        """The case the old arithmetic could not express.

        Stepping by the window width reached 23 of these 46 positions. Stepping
        by what was actually spent reaches all of them, which is the difference
        between covering the operator's list and sampling half of it.
        """
        reached, offset = set(), 0
        for _ in range(self.TOTAL * 3):
            reached.add(offset)
            offset = resume_after(offset, self.SERVED, 0, self.TOTAL)
        self.assertEqual(len(reached), self.TOTAL)

    def test_stepping_by_the_window_width_is_what_missed_half_of_them(self):
        """Recorded so the regression is recognisable if it comes back."""
        reached, offset = set(), 0
        for _ in range(self.TOTAL * 3):
            reached.add(offset)
            offset = (offset + self.SERVED) % self.TOTAL
        self.assertEqual(len(reached), 23)


class SweepRotaTests(unittest.TestCase):
    """A budget narrower than the list has to come round, not repeat itself.

    The first version of this served the first window every time, so a list of
    ten terms had four searched every evening and six never searched once, while
    the sweep reported success. These are the arithmetic that cannot regress.
    """

    TERMS = [f"term-{index}" for index in range(10)]

    def _every_window(self, size, sweeps):
        """Run the rota the way consecutive sweeps do, carrying the offset."""
        windows = []
        offset = 0
        for _ in range(sweeps):
            rota = next_window(self.TERMS, size, offset)
            windows.append(rota.terms)
            offset = rota.next_offset
        return windows

    def test_consecutive_sweeps_cover_the_whole_list(self):
        windows = self._every_window(size=4, sweeps=3)

        self.assertEqual(windows[0], self.TERMS[0:4])
        self.assertEqual(windows[1], self.TERMS[4:8])
        served = [term for window in windows for term in window]
        self.assertEqual(set(served), set(self.TERMS))

    def test_a_window_wraps_round_the_end_rather_than_stopping_short(self):
        rota = next_window(self.TERMS, 4, offset=8)

        self.assertEqual(rota.terms, ["term-8", "term-9", "term-0", "term-1"])
        self.assertEqual(rota.start, 8)
        self.assertEqual(rota.next_offset, 2)

    def test_the_rota_returns_to_where_it_began(self):
        offset = 0
        for _ in range(5):
            offset = next_window(self.TERMS, 4, offset).next_offset
        self.assertEqual(offset, 0)

    def test_a_stored_offset_past_the_end_is_not_an_error(self):
        """A shortened list must not leave the sink slicing past the end."""
        rota = next_window(self.TERMS, 3, offset=97)

        self.assertEqual(rota.start, 7)
        self.assertEqual(rota.terms, ["term-7", "term-8", "term-9"])

    def test_a_budget_wider_than_the_list_serves_it_once_from_the_start(self):
        """Otherwise the console announces terms 6-15 of 10."""
        rota = next_window(self.TERMS, 50, offset=5)

        self.assertEqual(rota.terms, self.TERMS)
        self.assertEqual(rota.start, 0)
        self.assertEqual(rota.next_offset, 0)

    def test_a_budget_of_none_serves_nothing_and_does_not_lose_its_place(self):
        rota = next_window(self.TERMS, 0, offset=4)

        self.assertEqual(rota.terms, [])
        self.assertEqual(rota.next_offset, 4)

    def test_a_negative_budget_cannot_hand_out_more_than_the_list(self):
        """It used to slice backwards and serve a window twice the list long."""
        rota = next_window(self.TERMS, -1, offset=0)

        self.assertEqual(rota.terms, [])

    def test_no_terms_is_answered_rather_than_divided_by(self):
        self.assertEqual(next_window([], 4, offset=3), ([], 0, 0, 0))

    def test_the_console_names_both_runs_when_a_window_wraps(self):
        """Reported as one run it reads "terms 9-12 of 10"."""
        self.assertEqual(
            next_window(self.TERMS, 4, offset=8).description(),
            "terms 9-10 and 1-2 of 10",
        )

    def test_the_console_names_one_run_when_a_window_does_not_wrap(self):
        self.assertEqual(
            next_window(self.TERMS, 4, offset=4).description(),
            "terms 5-8 of 10",
        )

    def test_the_console_says_so_when_a_sweep_gets_nothing(self):
        self.assertEqual(
            next_window(self.TERMS, 0, offset=4).description(),
            "no terms this sweep, of 10",
        )


class SweptTodayTests(unittest.TestCase):
    """The gate that decides whether the morning mail has anything to say.

    It has to fail closed. The first version compared two captured strings in
    a .cmd file, both came out empty, empty equalled empty, and a run went out
    on the one morning the gate existed to stop.
    """

    TODAY = "2026-10-06"

    def test_a_note_from_today_settles_it(self):
        self.assertFalse(sweep_is_due({"date": self.TODAY}, self.TODAY))

    def test_a_note_from_yesterday_owes_a_sweep(self):
        self.assertTrue(sweep_is_due({"date": "2026-10-05"}, self.TODAY))

    def test_nothing_recorded_owes_a_sweep(self):
        self.assertTrue(sweep_is_due(None, self.TODAY))

    def test_an_unreadable_note_owes_a_sweep_rather_than_raising(self):
        for junk in ("not json at all", [], 7, {"date": 20261006}, {}):
            with self.subTest(junk=junk):
                self.assertEqual(last_sweep_date(junk), "")
                self.assertTrue(sweep_is_due(junk, self.TODAY))

    def test_a_date_from_the_future_asks_for_one_more_sweep(self):
        """A clock that moved should not silence the mail for ever."""
        self.assertTrue(sweep_is_due({"date": "2027-01-01"}, self.TODAY))


class MailFreshnessTests(unittest.TestCase):
    """The gate that decides whether the morning mail has anything to say.

    It is not the companion's question, and two earlier versions of it each
    silenced the feature completely.

    Asking the calendar question was the first. A sweep runs in the evening
    and the mail goes at seven, so a same-date test rejects the freshest
    records that exist; the run log recorded "no sweep today, nothing sent" at
    07:00 on a morning whose sweep had finished seven hours earlier.

    Asking whether a sweep had finished was the second, and it is the reason
    this reads records rather than the companion's note. A sweep is not
    reliably allowed to finish: Amazon throttles it after a term or two and it
    abandons the attempt. One night of those truncated attempts recorded 278
    rows across fifteen searches, and the gate still sent nothing, because no
    sweep had ever reported itself done.

    Like the companion's gate it has to fail closed, for the same reason: an
    earlier version compared two captured strings in a .cmd file, both came
    out empty, empty equalled empty, and a run went out.
    """

    NOW = datetime(2026, 10, 8, 7, 0, tzinfo=UTC)

    def arrived(self, hours_ago):
        """One row's arrival stamp, as the sink writes it."""
        return (self.NOW - timedelta(hours=hours_ago)).isoformat()

    def freshest(self, *hours_ago):
        return newest_moment(self.arrived(hours) for hours in hours_ago)

    def test_last_nights_records_are_what_the_mail_is_for(self):
        """The case the calendar test got wrong, and the reason this exists."""
        evening = self.freshest(8)
        self.assertNotEqual(
            evening.date().isoformat(), self.NOW.date().isoformat()
        )
        self.assertTrue(is_recent(evening, self.NOW))

    def test_a_truncated_sweep_that_recorded_something_still_mails(self):
        """The second bug: no sweep ever finished, yet rows kept arriving."""
        self.assertTrue(sweep_is_due({}, self.NOW.date().isoformat()))
        self.assertTrue(is_recent(self.freshest(1), self.NOW))

    def test_a_record_just_inside_the_window_still_counts(self):
        self.assertTrue(is_recent(self.freshest(MAIL_WINDOW_HOURS - 0.1), self.NOW))

    def test_a_record_past_the_window_sends_nothing(self):
        self.assertFalse(is_recent(self.freshest(MAIL_WINDOW_HOURS + 0.1), self.NOW))

    def test_a_day_with_chrome_closed_sends_nothing(self):
        self.assertFalse(is_recent(self.freshest(30), self.NOW))

    def test_the_newest_row_decides_and_older_ones_do_not_drag_it_down(self):
        """A day's file holds every sighting, most of them older than the last."""
        self.assertTrue(is_recent(self.freshest(40, 30, 2, 25), self.NOW))

    def test_every_unreadable_shape_fails_closed(self):
        for junk in (None, [], 7, {}, "not a timestamp", "", 1760000000, True):
            with self.subTest(junk=junk):
                self.assertIsNone(moment_from(junk))
                self.assertFalse(is_recent(moment_from(junk), self.NOW))

    def test_no_records_at_all_sends_nothing(self):
        self.assertIsNone(newest_moment([]))
        self.assertFalse(is_recent(newest_moment([]), self.NOW))

    def test_one_corrupt_row_does_not_hide_the_rest_of_the_day(self):
        mixed = ["not a timestamp", None, self.arrived(3), 7]
        self.assertTrue(is_recent(newest_moment(mixed), self.NOW))

    def test_a_date_with_no_time_is_not_an_instant(self):
        """A bare date could mean any of twenty-four hours, so it is declined."""
        self.assertFalse(is_recent(moment_from("2026-10-08"), self.NOW))

    def test_small_clock_skew_between_two_processes_is_tolerated(self):
        self.assertTrue(is_recent(self.freshest(-0.05), self.NOW))

    def test_a_clock_far_in_the_future_declines_rather_than_mails(self):
        self.assertFalse(is_recent(self.freshest(-5), self.NOW))

    def test_a_naive_timestamp_is_read_as_utc_rather_than_rejected(self):
        naive = (self.NOW - timedelta(hours=3)).replace(tzinfo=None)
        self.assertTrue(is_recent(moment_from(naive.isoformat()), self.NOW))

    def test_the_two_questions_stay_separate(self):
        """The companion sweeps once a local day; the mail reads the records."""
        note = {"date": (self.NOW - timedelta(days=1)).date().isoformat()}
        self.assertTrue(sweep_is_due(note, self.NOW.date().isoformat()))
        self.assertEqual(last_sweep_date(note), "2026-10-07")
        self.assertTrue(is_recent(self.freshest(8), self.NOW))

    def test_the_age_is_reported_for_an_operator_to_read(self):
        self.assertEqual(describe_age(self.freshest(7), self.NOW), "7.0 hours ago")
        self.assertEqual(describe_age(self.freshest(0.5), self.NOW), "30 minutes ago")
        self.assertEqual(describe_age(None, self.NOW), "never")


class PreflightingAProviderThatCannotFetchTests(unittest.TestCase):
    """`doctor` is the only preflight an unattended run gets, so it has to run.

    Vine's whole point is that it cannot be asked for lots, and for a while
    that made this command refuse before reaching the mail settings -- which
    are the thing such a run actually fails on at seven in the morning.
    """

    def test_the_registry_states_which_providers_fetch(self):
        self.assertFalse(resolve_provider("vine").discovers)
        self.assertTrue(resolve_provider("nellis").discovers)

    def test_a_manual_read_only_provider_is_preflighted_rather_than_refused(self):
        with temporary_directory() as directory:
            config = Path(directory) / "vine.toml"
            config.write_text(
                '[provider]\nid = "vine"\n'
                '[provider.acquisition]\nmode = "manual"\n'
                'searches = ["synthetic term"]\n',
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config),
                email=False,
                webhook=False,
                delivery_ledger=str(Path(directory) / "deliveries.sqlite3"),
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(doctor(args), 0)

        said = output.getvalue()
        self.assertIn("nothing here fetches", said)
        # The checks past the refusal are the whole reason to run this at all.
        self.assertIn("no network requests were made", said)

    def test_a_fetching_provider_still_requires_production_run_mode(self):
        """The guard this skips for Vine must stay where it means something."""
        with temporary_directory() as directory:
            config = Path(directory) / "nellis.toml"
            config.write_text(
                '[provider]\nid = "nellis"\n'
                '[provider.acquisition]\nrun_mode = "development"\n'
                'searches = ["synthetic term"]\n',
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config),
                email=False,
                webhook=False,
                delivery_ledger=str(Path(directory) / "deliveries.sqlite3"),
            )
            with self.assertRaises(RuntimeError) as caught:
                doctor(args)
        self.assertIn("run_mode", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
