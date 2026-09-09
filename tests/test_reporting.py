"""Rendering findings and delivering them."""

from __future__ import annotations

import os
import ssl
import unittest
from dataclasses import replace
from decimal import Decimal
from inspect import signature
from unittest.mock import patch

from auction_lens.env_file import load_env_file
from auction_lens.models import WatchedItem
from auction_lens.reporting import (
    build_report,
    check_email_ready,
    render_html,
    render_text,
    send_email,
    send_watchlist_email,
)
from auction_lens.scoring import evaluate
from support import (
    LASER_LEVEL,
    REPORT_ZONE,
    SOUNDBAR,
    example_config,
    example_listings,
    temporary_directory,
)

SMTP_ENVIRONMENT = {
    "AUCTION_LENS_SMTP_HOST": "smtp.example.invalid",
    "AUCTION_LENS_SMTP_USERNAME": "user",
    "AUCTION_LENS_SMTP_PASSWORD": "secret",
    "AUCTION_LENS_EMAIL_FROM": "sender@example.invalid",
    "AUCTION_LENS_EMAIL_TO": "recipient@example.invalid",
}


class TextReportTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_report_states_the_actionable_cost(self):
        report = render_text(evaluate(self.listings[LASER_LEVEL], self.config), REPORT_ZONE)
        self.assertIn("Estimated total: $12.65", report)
        self.assertIn("Example Laser Level Kit", report)

    def test_empty_report_says_so_plainly(self):
        self.assertIn("no listings", render_text([], REPORT_ZONE))

    def test_open_handling_question_is_shown_with_its_decision_key(self):
        listing = replace(
            self.listings[SOUNDBAR],
            handling_weight_lb=Decimal("148"),
            package_dimensions_in=(Decimal("70"), Decimal("31"), Decimal("45")),
            loading_assistance=("forklift",),
        )
        candidate = next(
            item for item in evaluate(listing, self.config) if item.category == "wanted"
        )
        report = render_text([candidate], REPORT_ZONE)
        self.assertIn("LOGISTICS CHECK", report)
        self.assertIn("Decision key: nellis/synthetic-001", report)


class ClosingTimeTests(unittest.TestCase):
    """When bidding ends, which is the one fact a deadline report must carry.

    The closing time was fetched, parsed and stored from the beginning, and for
    a long while no renderer printed it: a report could say a lot was ending
    soon without ever saying when. These tests hold the reporting end of that.
    """

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _wanted(self, index):
        return [
            item
            for item in evaluate(self.listings[index], self.config)
            if item.category == "wanted"
        ]

    def test_a_closing_time_is_read_against_the_providers_clock(self):
        # The fixture closes at 02:30 UTC, which is the evening before in
        # Phoenix. Asserting the local reading means a report that skipped the
        # conversion would fail here rather than be off by seven hours in
        # silence -- and it would name the wrong weekday while it did it.
        report = render_text(self._wanted(SOUNDBAR), REPORT_ZONE)
        self.assertIn("Closes: Mon 19:30 MST", report)
        self.assertNotIn("02:30", report)

    def test_the_same_closing_time_reaches_the_html_report(self):
        self.assertIn("Mon 19:30 MST", render_html(self._wanted(SOUNDBAR), REPORT_ZONE))

    def test_a_lot_with_no_stated_closing_time_claims_none(self):
        # Rather than inventing a deadline, or printing an empty label that
        # reads like the auction never ends.
        report = render_text(evaluate(self.listings[LASER_LEVEL], self.config), REPORT_ZONE)
        self.assertIn("Example Laser Level Kit", report)
        self.assertNotIn("Closes", report)


class HtmlReportTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_card_shows_the_listing_and_links_to_it(self):
        candidates = evaluate(self.listings[SOUNDBAR], self.config)
        report = render_html(candidates, REPORT_ZONE)
        self.assertIn("Example 2.1 Channel Sound Bar with ARC", report)
        self.assertIn("https://example.invalid/auction/synthetic-001", report)
        self.assertIn("Estimated total: $20.70", report)

    def test_card_links_product_and_actual_lot_photos_to_the_listing(self):
        candidate = evaluate(self.listings[SOUNDBAR], self.config)[0]

        report = render_html([candidate], REPORT_ZONE)

        listing_link = "<a href='https://example.invalid/auction/synthetic-001'>"
        self.assertEqual(report.count(listing_link), 3)  # Two photos and View listing.
        self.assertIn("<strong>Product photo</strong>", report)
        self.assertIn("synthetic-001-stock.jpg", report)
        self.assertIn("<strong>Actual lot</strong>", report)
        self.assertIn("synthetic-001-shelf.jpg", report)

    def test_one_gallery_image_is_shown_once_with_an_honest_label(self):
        candidate = evaluate(self.listings[SOUNDBAR], self.config)[0]
        listing = replace(
            candidate.listing,
            photo_urls=("https://example.invalid/photo/only.jpg",),
        )

        report = render_html([replace(candidate, listing=listing)], REPORT_ZONE)

        self.assertEqual(report.count("src='https://example.invalid/photo/only.jpg'"), 1)
        self.assertIn("<strong>Listing photo</strong>", report)
        self.assertNotIn("<strong>Product photo</strong>", report)
        self.assertNotIn("<strong>Actual lot</strong>", report)

    def test_photo_and_listing_addresses_are_escaped(self):
        candidate = evaluate(self.listings[SOUNDBAR], self.config)[0]
        listing = replace(
            candidate.listing,
            url="https://example.invalid/lot?next='details'&view=full",
            photo_urls=("https://example.invalid/lot.jpg?size='full'&crop=none",),
        )

        report = render_html([replace(candidate, listing=listing)], REPORT_ZONE)

        self.assertIn("next=&#x27;details&#x27;&amp;view=full", report)
        self.assertIn("size=&#x27;full&#x27;&amp;crop=none", report)

    def test_non_https_photos_are_not_embedded_in_email(self):
        candidate = evaluate(self.listings[SOUNDBAR], self.config)[0]
        listing = replace(
            candidate.listing,
            photo_urls=("http://example.invalid/photo/lot.jpg",),
        )

        report = render_html([replace(candidate, listing=listing)], REPORT_ZONE)

        self.assertNotIn("<img", report)

    def test_one_secure_end_of_a_gallery_is_still_shown(self):
        candidate = evaluate(self.listings[SOUNDBAR], self.config)[0]
        listing = replace(
            candidate.listing,
            photo_urls=(
                "http://example.invalid/photo/stock.jpg",
                "https://example.invalid/photo/lot.jpg",
            ),
        )

        report = render_html([replace(candidate, listing=listing)], REPORT_ZONE)

        self.assertNotIn("stock.jpg", report)
        self.assertIn("<strong>Actual lot</strong>", report)
        self.assertIn("https://example.invalid/photo/lot.jpg", report)

    def test_listing_title_is_escaped(self):
        candidate = evaluate(self.listings[SOUNDBAR], self.config)[0]
        listing = replace(candidate.listing, title="<script>alert(1)</script>")
        report = render_html([replace(candidate, listing=listing)], REPORT_ZONE)
        self.assertNotIn("<script>", report)
        self.assertIn("&lt;script&gt;", report)

    def test_empty_report_says_so_plainly(self):
        self.assertIn("no listings", render_html([], REPORT_ZONE))


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.email = replace(self.config.email, enabled=True)
        self.candidates = evaluate(example_listings()[LASER_LEVEL], self.config)

    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_implicit_tls_verifies_the_server_certificate(self, smtp_ssl):
        with patch.dict("os.environ", SMTP_ENVIRONMENT, clear=False):
            send_email(self.candidates, self.email, REPORT_ZONE)

        context = smtp_ssl.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        smtp_ssl.assert_called_once_with(
            "smtp.example.invalid", 465, timeout=30, context=context
        )
        smtp_ssl.return_value.__enter__.return_value.send_message.assert_called_once()

    @patch("auction_lens.reporting.delivery.smtplib.SMTP")
    def test_starttls_verifies_the_server_certificate(self, smtp):
        email = replace(self.email, port=587, security="starttls")
        with patch.dict("os.environ", SMTP_ENVIRONMENT, clear=False):
            send_email(self.candidates, email, REPORT_ZONE)

        smtp.assert_called_once_with("smtp.example.invalid", 587, timeout=30)
        connection = smtp.return_value.__enter__.return_value
        context = connection.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        connection.starttls.assert_called_once_with(context=context)
        connection.login.assert_called_once_with("user", "secret")

    @patch("auction_lens.reporting.delivery.smtplib.SMTP")
    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_readiness_checks_settings_without_connecting(self, smtp_ssl, smtp):
        with patch.dict("os.environ", SMTP_ENVIRONMENT, clear=False):
            self.assertIsNone(check_email_ready(self.email))
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()

    def test_disabled_email_is_not_ready(self):
        with self.assertRaisesRegex(RuntimeError, "email reporting is disabled"):
            check_email_ready(replace(self.email, enabled=False))

    @patch("auction_lens.reporting.delivery.smtplib.SMTP")
    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_public_senders_refuse_disabled_email(self, smtp_ssl, smtp):
        disabled = replace(self.email, enabled=False)
        watchlist = (WatchedItem(source="nellis", listing_id="1", title="Lot"),)
        # Each sender takes its own arguments, so each is paired with the call
        # that exercises it rather than with a payload some caller has to shape.
        senders = (
            ("send_email", lambda: send_email(self.candidates, disabled, REPORT_ZONE)),
            ("send_watchlist_email", lambda: send_watchlist_email(watchlist, disabled)),
        )
        with patch.dict("os.environ", SMTP_ENVIRONMENT, clear=False):
            for name, send in senders:
                with self.subTest(sender=name):
                    with self.assertRaisesRegex(RuntimeError, "email reporting is disabled"):
                        send()
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()

    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_missing_settings_are_named_before_connecting(self, smtp_ssl):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "missing email environment settings"):
                send_email(self.candidates, self.email, REPORT_ZONE)
        smtp_ssl.assert_not_called()

    @patch("auction_lens.reporting.delivery.smtplib.SMTP")
    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_unknown_security_is_refused_without_connecting(self, smtp_ssl, smtp):
        with self.assertRaisesRegex(ValueError, "security must be one of: ssl, starttls"):
            send_email([], replace(self.email, security="starttlz"), REPORT_ZONE)
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()

    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_a_watchlist_email_has_a_clear_subject_and_both_renderings(self, smtp_ssl):
        items = (WatchedItem(source="nellis", listing_id="1", title="Flagged monitor"),)
        with patch.dict("os.environ", SMTP_ENVIRONMENT, clear=False):
            send_watchlist_email(items, self.email)

        message = smtp_ssl.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(message["Subject"], "Auction Lens watchlist: 1 selected lot")
        plain = message.get_body(preferencelist=("plain",)).get_content()
        markup = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Flagged monitor", plain)
        self.assertIn("Flagged monitor", markup)

    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_a_daily_email_includes_product_and_actual_lot_photos(self, smtp_ssl):
        with patch.dict("os.environ", SMTP_ENVIRONMENT, clear=False):
            send_email(self.candidates, self.email, REPORT_ZONE)

        message = smtp_ssl.return_value.__enter__.return_value.send_message.call_args.args[0]
        markup = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("synthetic-002-shelf.jpg", markup)
        self.assertIn("synthetic-002-stock.jpg", markup)

    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_a_watchlist_email_does_not_expose_its_local_file_path(self, smtp_ssl):
        items = (WatchedItem(source="nellis", listing_id="1", title="Flagged monitor"),)
        with patch.dict("os.environ", SMTP_ENVIRONMENT, clear=False):
            send_watchlist_email(items, self.email)

        message = smtp_ssl.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertNotIn("C:\\Users", message.as_string())
        self.assertIn("Flagged monitor", message.as_string())

    def test_there_is_no_parameter_a_local_path_could_arrive_through(self):
        # The terminal report still shows the path, because that is your own
        # screen. An email leaves the machine, so the guarantee is structural
        # rather than a caller remembering not to pass one.
        taken = signature(send_watchlist_email).parameters
        self.assertNotIn("path", taken)


class BothRenderingsSayTheSameThingTests(unittest.TestCase):
    """The reason findings and rendering are separate modules.

    Text and HTML used to walk a candidate independently, so each could quietly
    grow a fact the other did not have. Now there is one list of facts, and this
    proves both renderings show all of it.
    """

    def setUp(self):
        config = example_config()
        listing = replace(
            example_listings()[SOUNDBAR],
            handling_weight_lb=Decimal("148"),
            package_dimensions_in=(Decimal("70"), Decimal("31"), Decimal("45")),
        )
        self.candidates = evaluate(listing, config)
        self.report = build_report(self.candidates, REPORT_ZONE)

    def _findings(self):
        return [finding for group in self.report.groups for finding in group.findings]

    def test_every_fact_reaches_both_renderings(self):
        plain = render_text(self.candidates, REPORT_ZONE)
        markup = render_html(self.candidates, REPORT_ZONE)
        for finding in self._findings():
            for fact in finding.facts:
                self.assertIn(fact.value, plain, f"{fact.label} missing from text")
                self.assertIn(fact.value, markup, f"{fact.label} missing from HTML")

    def test_every_open_handling_question_reaches_both_renderings(self):
        plain = render_text(self.candidates, REPORT_ZONE)
        markup = render_html(self.candidates, REPORT_ZONE)
        asked = [q for finding in self._findings() for q in finding.handling.questions]
        self.assertTrue(asked, "this fixture is meant to raise handling questions")
        for question in asked:
            self.assertIn(question, plain)
            self.assertIn(question, markup)


class EnvironmentFileTests(unittest.TestCase):
    def test_file_values_never_override_the_process_environment(self):
        with temporary_directory() as directory:
            env_file = directory / ".env"
            env_file.write_text(
                "NEW_SETTING=from-file\nEXISTING_SETTING=from-file\n", encoding="utf-8"
            )
            with patch.dict("os.environ", {"EXISTING_SETTING": "from-process"}, clear=True):
                load_env_file(env_file)
                self.assertEqual(os.environ["NEW_SETTING"], "from-file")
                self.assertEqual(os.environ["EXISTING_SETTING"], "from-process")

    def test_quoted_values_and_comments_are_understood(self):
        with temporary_directory() as directory:
            env_file = directory / ".env"
            env_file.write_text(
                '# a comment\n\nQUOTED="with spaces"\n', encoding="utf-8"
            )
            with patch.dict("os.environ", {}, clear=True):
                load_env_file(env_file)
                self.assertEqual(os.environ["QUOTED"], "with spaces")

    def test_a_line_without_an_assignment_is_reported(self):
        with temporary_directory() as directory:
            env_file = directory / ".env"
            env_file.write_text("BROKEN LINE\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid environment line 1"):
                load_env_file(env_file)

    def test_a_missing_file_is_not_an_error(self):
        with temporary_directory() as directory:
            load_env_file(directory / "absent.env")


if __name__ == "__main__":
    unittest.main()
