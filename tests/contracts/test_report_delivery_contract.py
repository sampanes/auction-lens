"""One compact contract for what a daily report says in every delivery channel."""

from __future__ import annotations

import json
import smtplib
import unittest
from dataclasses import replace
from decimal import Decimal
from html import unescape
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from auction_lens.config.load import load_config
from auction_lens.config.reports import WebhookConfig
from auction_lens.listings.files import load_listings
from auction_lens.listings.model import ObservationChange
from auction_lens.matching.evaluate import evaluate
from auction_lens.matching.progress import InterestProgress, InterestRef
from auction_lens.pricing.model import ResearchLink, ValuationBand, ValuationSummary
from auction_lens.reports.email import send_email
from auction_lens.reports.findings import build_report
from auction_lens.reports.html import render_html
from auction_lens.reports.records import DeliverySummary
from auction_lens.reports.text import render_text
from auction_lens.reports.webhook import build_message, send_webhook

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config" / "providers" / "nellis.example.toml"
SYNTHETIC_LISTINGS = ROOT / "fixtures" / "synthetic" / "listings.json"
REPORT_ZONE = ZoneInfo("America/Phoenix")
SOUNDBAR = 0

MAIL_ENVIRONMENT = {
    "AUCTION_LENS_SMTP_HOST": "smtp.example.invalid",
    "AUCTION_LENS_SMTP_USERNAME": "synthetic-user",
    "AUCTION_LENS_SMTP_PASSWORD": "synthetic-password",
    "AUCTION_LENS_EMAIL_FROM": "sender@example.invalid",
    "AUCTION_LENS_EMAIL_TO": "reader@example.invalid",
}
WEBHOOK_ENVIRONMENT = {
    "AUCTION_LENS_WEBHOOK_URL": "https://example.invalid/hooks/synthetic-secret"
}

# These sentences are the cross-channel promises: a reader must be able to
# identify the lot, decide whether it is interesting, and see what needs action.
SHARED_REPORT_FACTS = (
    "Example 2.1 Channel Sound Bar with ARC",
    "$20.70",
    "$129.00",
    "Mon 19:30 MST",
    "Example Warehouse",
    "nellis/synthetic-001",
    "matches use interest 'soundbar'",
    "Collection incomplete: 1 provider page could not be read; this report may omit listings.",
    "Only new or price-changed matches are included in this delivery.",
    "2 unchanged matches were already delivered here.",
    "1 more new or changed match was held back by this report's limit.",
    "soundbar: 1/2 fulfilled; 1 remaining",
    "1 won lot has an unreviewed finite-interest match",
)


class RecordingSmtp:
    """Accept a message like SMTP_SSL while keeping the test off the network."""

    sent = []

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def login(self, _username, _password):
        pass

    def send_message(self, message):
        self.sent.append(message)


class RecordingWebhook:
    """Act as the injected HTTPS opener and retain the outgoing request."""

    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return EmptyResponse()


class EmptyResponse:
    """The context-managed response shape the webhook transport consumes."""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b""


class DailyReportDeliveryContract(unittest.TestCase):
    """Protect semantics at the public render and delivery boundaries."""

    def setUp(self):
        config = load_config(EXAMPLE_CONFIG)
        listing = replace(
            load_listings(SYNTHETIC_LISTINGS)[SOUNDBAR],
            handling_weight_lb=Decimal("148"),
            package_dimensions_in=(Decimal("70"), Decimal("31"), Decimal("45")),
            loading_assistance=("forklift",),
        )
        wanted = next(
            candidate
            for candidate in evaluate(listing, config)
            if candidate.category == "wanted"
        )
        wanted = replace(
            wanted,
            change=ObservationChange(
                is_new=False,
                price_changed=True,
                previous_bid=Decimal("16.00"),
            ),
            valuation=ValuationSummary(
                bands=(
                    ValuationBand(
                        basis="used",
                        low=Decimal("70"),
                        typical=Decimal("85"),
                        high=Decimal("100"),
                        source_count=2,
                        sample_size=7,
                    ),
                ),
                research_links=(
                    ResearchLink(
                        source_id="synthetic-market",
                        label="Synthetic sold listings",
                        url="https://example.invalid/research/sb21",
                    ),
                ),
                errors=("synthetic guide: temporarily unavailable",),
            ),
        )
        self.candidate = wanted
        self.config = config
        self.report = build_report(
            [wanted],
            REPORT_ZONE,
            interest_progress=(
                InterestProgress(
                    interest=InterestRef(interest_id="audio", name="soundbar"),
                    wanted=2,
                    fulfilled=1,
                ),
            ),
            unreviewed_wins=1,
            notices=(
                "Collection incomplete: 1 provider page could not be read; "
                "this report may omit listings.",
            ),
            delivery=DeliverySummary(
                active=True,
                unchanged_matches=2,
                unchanged_titles=("Already seen amp", "Already seen keyboard"),
                held_back_matches=1,
            ),
        )

    def test_text_and_html_keep_the_complete_reader_contract(self):
        plain = render_text(self.report)
        markup = render_html(self.report)
        visible_markup = unescape(markup)

        for fact in SHARED_REPORT_FACTS:
            with self.subTest(fact=fact):
                self.assertIn(fact.casefold(), plain.casefold())
                self.assertIn(fact.casefold(), visible_markup.casefold())

        full_report_only = (
            "price changed from $16.00",
            "Confirm the 70 x 31 x 45 in item fits the planned transport.",
            "Used: $70-$100 (typical $85; 2 source(s), 7 comp(s))",
            "Synthetic sold listings",
            "synthetic guide: temporarily unavailable",
        )
        for fact in full_report_only:
            with self.subTest(full_report_fact=fact):
                self.assertIn(fact.casefold(), plain.casefold())
                self.assertIn(fact.casefold(), visible_markup.casefold())

        # Photos are useful in rich mail, but remote media never bloats the
        # plain body or turns into an attachment.
        self.assertNotIn("synthetic-001-stock.jpg", plain)
        self.assertNotIn("synthetic-001-shelf.jpg", plain)
        self.assertIn("synthetic-001-stock.jpg", markup)
        self.assertIn("synthetic-001-shelf.jpg", markup)

    def test_email_is_a_multipart_copy_of_the_two_public_renderings(self):
        email = replace(
            self.config.email,
            enabled=True,
            subject="Auction Lens: {{ match_count }} lot, closes {{ first_close }}",
        )
        RecordingSmtp.sent = []

        with (
            patch.dict("os.environ", MAIL_ENVIRONMENT, clear=True),
            patch.object(smtplib, "SMTP_SSL", RecordingSmtp),
        ):
            send_email(self.report, email)

        self.assertEqual(len(RecordingSmtp.sent), 1)
        message = RecordingSmtp.sent[0]
        self.assertEqual(message["Subject"], "Auction Lens: 1 lot, closes Mon 19:30 MST")
        self.assertEqual(message["From"], "sender@example.invalid")
        self.assertEqual(message["To"], "reader@example.invalid")
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertEqual(
            message.get_body(preferencelist=("plain",)).get_content(),
            render_text(self.report),
        )
        self.assertEqual(
            message.get_body(preferencelist=("html",)).get_content().rstrip("\n"),
            render_html(self.report),
        )
        self.assertEqual(list(message.iter_attachments()), [])

    def test_webhook_keeps_the_actionable_subset_and_report_context(self):
        webhook = RecordingWebhook()
        config = WebhookConfig(enabled=True, username="Synthetic Auction Lens")

        with patch.dict("os.environ", WEBHOOK_ENVIRONMENT, clear=True):
            send_webhook(self.report, config, opener=webhook)

        self.assertEqual(len(webhook.requests), 1)
        request, timeout = webhook.requests[0]
        self.assertEqual(request.full_url, WEBHOOK_ENVIRONMENT["AUCTION_LENS_WEBHOOK_URL"])
        self.assertEqual(request.method, "POST")
        self.assertGreater(timeout, 0)
        payload = json.loads(request.data)

        self.assertEqual(payload["username"], "Synthetic Auction Lens")
        for fact in SHARED_REPORT_FACTS[7:]:
            with self.subTest(report_context=fact):
                self.assertIn(fact, payload["content"])

        self.assertEqual(
            payload["embeds"],
            [
                {
                    "title": "Example 2.1 Channel Sound Bar with ARC",
                    "url": "https://example.invalid/auction/synthetic-001",
                    "color": 12986408,
                    "fields": [
                        {"name": "Cost", "value": "$20.70", "inline": True},
                        {"name": "Retail", "value": "$129.00 (16%)", "inline": True},
                        {
                            "name": "Where",
                            "value": "Example Warehouse",
                            "inline": True,
                        },
                        {"name": "Closes", "value": "Mon 19:30 MST", "inline": True},
                        {
                            "name": "Condition",
                            "value": "used, missing parts unknown",
                            "inline": False,
                        },
                        {
                            "name": "Why",
                            "value": "matches use interest 'soundbar'",
                            "inline": False,
                        },
                        {
                            "name": "Watch key",
                            "value": "nellis/synthetic-001",
                            "inline": False,
                        },
                    ],
                }
            ],
        )

    def test_provider_condition_words_are_identical_in_every_channel(self):
        candidate = replace(
            self.candidate,
            listing=replace(
                self.candidate.listing,
                conditions=("Used", "No Damage"),
            ),
        )
        report = build_report([candidate], REPORT_ZONE)
        plain = render_text(report)
        markup = unescape(render_html(report))
        payload = build_message(report, WebhookConfig(enabled=True))
        condition = next(
            field["value"]
            for field in payload["embeds"][0]["fields"]
            if field["name"] == "Condition"
        )

        self.assertEqual(condition, "Used, No Damage")
        self.assertIn("Conditions: Used, No Damage", plain)
        self.assertIn("Conditions: Used, No Damage", markup)


if __name__ == "__main__":
    unittest.main()
