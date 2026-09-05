"""Rendering findings and delivering them."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from auction_lens.env_file import load_env_file
from auction_lens.reporting import render_html, render_text, send_email
from auction_lens.scoring import evaluate
from support import (
    LASER_LEVEL,
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
        report = render_text(evaluate(self.listings[LASER_LEVEL], self.config))
        self.assertIn("estimated total $12.65", report)
        self.assertIn("Example Laser Level Kit", report)

    def test_empty_report_says_so_plainly(self):
        self.assertIn("no listings", render_text([]))

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
        report = render_text([candidate])
        self.assertIn("LOGISTICS CHECK", report)
        self.assertIn("Decision key: nellis/synthetic-001", report)


class HtmlReportTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_card_shows_the_listing_and_links_to_it(self):
        candidates = evaluate(self.listings[SOUNDBAR], self.config)
        report = render_html(candidates)
        self.assertIn("Example 2.1 Channel Sound Bar with ARC", report)
        self.assertIn("https://example.invalid/auction/synthetic-001", report)
        self.assertIn("Estimated total $20.70", report)

    def test_listing_title_is_escaped(self):
        candidate = evaluate(self.listings[SOUNDBAR], self.config)[0]
        listing = replace(candidate.listing, title="<script>alert(1)</script>")
        report = render_html([replace(candidate, listing=listing)])
        self.assertNotIn("<script>", report)
        self.assertIn("&lt;script&gt;", report)

    def test_empty_report_says_so_plainly(self):
        self.assertIn("no listings", render_html([]))


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.candidates = evaluate(example_listings()[LASER_LEVEL], self.config)

    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_credentials_come_from_the_environment(self, smtp_ssl):
        with patch.dict("os.environ", SMTP_ENVIRONMENT, clear=False):
            send_email(self.candidates, self.config.email)
        smtp_ssl.assert_called_once_with("smtp.example.invalid", 465, timeout=30)
        smtp_ssl.return_value.__enter__.return_value.send_message.assert_called_once()

    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_missing_settings_are_named_before_connecting(self, smtp_ssl):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "missing email environment settings"):
                send_email(self.candidates, self.config.email)
        smtp_ssl.assert_not_called()

    @patch("auction_lens.reporting.delivery.smtplib.SMTP")
    @patch("auction_lens.reporting.delivery.smtplib.SMTP_SSL")
    def test_unknown_security_is_refused_without_connecting(self, smtp_ssl, smtp):
        with self.assertRaisesRegex(ValueError, "email security must be"):
            send_email([], replace(self.config.email, security="starttlz"))
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()


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
