"""Posting a report to a chat webhook: what it says, and what it refuses to do."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import MagicMock, patch

from auction_lens.config import WebhookConfig
from auction_lens.grading import read_grade
from auction_lens.models import InterestProgress, InterestRef, ReadingOrder
from auction_lens.reporting import (
    DeliverySummary,
    build_report,
    check_webhook_ready,
    send_webhook,
    webhook_destination,
)
from auction_lens.reporting.webhook import (
    HIGHEST_CONTENT_LENGTH,
    HIGHEST_EMBED_COUNT,
    build_message,
    webhook_address,
    webhook_item_limit,
)
from auction_lens.scoring import evaluate
from support import REPORT_ZONE, SOUNDBAR, example_config, example_listings

ADDRESS = "https://discord.com/api/webhooks/000/secret-token"
ENVIRONMENT = {"AUCTION_LENS_WEBHOOK_URL": ADDRESS}


def _progress(name: str, wanted: int | None, fulfilled: int = 0) -> InterestProgress:
    return InterestProgress(
        interest=InterestRef(interest_id=name.casefold(), name=name),
        wanted=wanted,
        fulfilled=fulfilled,
    )


def _candidates(count: int = 1):
    config = example_config()
    listing = example_listings()[SOUNDBAR]
    found = [item for item in evaluate(listing, config) if item.category == "wanted"]
    return found * count


class MessageShapeTests(unittest.TestCase):
    def setUp(self):
        self.config = WebhookConfig(enabled=True)

    def test_every_card_carries_the_address_that_opens_the_listing(self):
        # The provider publishes app links for this address, so one link opens
        # the app on a phone and the site everywhere else. There is no second,
        # app-flavoured address to build.
        message = build_message(build_report(_candidates(), REPORT_ZONE), self.config)
        self.assertEqual(message["embeds"][0]["url"], _candidates()[0].listing.url)

    def test_every_card_carries_the_key_the_watch_command_accepts(self):
        message = build_message(build_report(_candidates(), REPORT_ZONE), self.config)
        fields = {
            field["name"]: field["value"]
            for field in message["embeds"][0]["fields"]
        }

        self.assertEqual(fields["Watch key"], "nellis/synthetic-001")

    def test_a_card_says_when_bidding_ends_in_the_providers_own_time(self):
        message = build_message(build_report(_candidates(), REPORT_ZONE), self.config)
        fields = {field["name"]: field["value"] for field in message["embeds"][0]["fields"]}
        self.assertEqual(fields["Closes"], "Mon 19:30 MST")

    def test_a_card_says_so_when_no_closing_time_was_published(self):
        # A chat card has a fixed set of fields, so the absence has to be a
        # word. An empty value would read as a rendering fault instead.
        undated = _candidates()
        undated[0] = replace(
            undated[0], listing=replace(undated[0].listing, ends_at=None)
        )
        message = build_message(build_report(undated, REPORT_ZONE), self.config)
        fields = {field["name"]: field["value"] for field in message["embeds"][0]["fields"]}
        self.assertEqual(fields["Closes"], "unstated")

    def test_it_never_sends_more_cards_than_the_service_accepts(self):
        many = replace(self.config, max_items=99)
        message = build_message(build_report(_candidates(40), REPORT_ZONE), many)
        self.assertEqual(len(message["embeds"]), HIGHEST_EMBED_COUNT)
        self.assertIn("the best 10 follow", message["content"])

    def test_a_smaller_limit_is_respected(self):
        few = replace(self.config, max_items=3)
        message = build_message(build_report(_candidates(40), REPORT_ZONE), few)
        self.assertEqual(len(message["embeds"]), 3)

    def test_the_public_limit_matches_the_cards_the_transport_will_render(self):
        configured = replace(self.config, max_items=7)

        self.assertEqual(webhook_item_limit(configured, None), 7)
        self.assertEqual(webhook_item_limit(configured, 4), 4)
        self.assertEqual(
            webhook_item_limit(replace(configured, max_items=99), None),
            HIGHEST_EMBED_COUNT,
        )

    def test_retail_order_rearranges_only_the_candidates_already_selected(self):
        candidates = _candidates(2)
        candidates[0] = replace(
            candidates[0],
            listing=replace(
                candidates[0].listing,
                listing_id="priority-first",
                title="Priority first",
                estimated_retail=Decimal("100"),
            ),
            score=90,
        )
        candidates[1] = replace(
            candidates[1],
            listing=replace(
                candidates[1].listing,
                listing_id="retail-first",
                title="Retail first",
                estimated_retail=Decimal("900"),
            ),
            score=40,
        )

        message = build_message(
            build_report(candidates, REPORT_ZONE, order=ReadingOrder.RETAIL),
            self.config,
        )

        self.assertEqual(
            [card["title"] for card in message["embeds"]],
            ["Retail first", "Priority first"],
        )

        capped = build_message(
            build_report(candidates, REPORT_ZONE, order=ReadingOrder.RETAIL),
            replace(self.config, max_items=1),
        )
        self.assertEqual([card["title"] for card in capped["embeds"]], ["Priority first"])

    def test_finding_nothing_still_says_so(self):
        message = build_message(build_report([], REPORT_ZONE), self.config)
        self.assertEqual(message["embeds"], [])
        self.assertIn("Nothing matched", message["content"])

    def test_empty_message_still_says_why_a_finite_interest_is_silent(self):
        progress = (
            _progress("soundbar", 1, 1),
            _progress("materials", None, 4),
        )

        message = build_message(
            build_report([], REPORT_ZONE, interest_progress=progress),
            self.config,
        )

        self.assertIn("soundbar: 1/1 fulfilled; retired", message["content"])
        self.assertNotIn("materials", message["content"])

    def test_unreviewed_wins_point_to_the_explicit_watch_action(self):
        message = build_message(
            build_report([], REPORT_ZONE, unreviewed_wins=2),
            self.config,
        )

        self.assertIn(
            "Action needed: 2 won lots have an unreviewed finite-interest match",
            message["content"],
        )
        self.assertIn("watchlist --verdict won", message["content"])

    def test_delivery_filter_counts_reach_chat(self):
        message = build_message(
            build_report(
                [],
                REPORT_ZONE,
                delivery=DeliverySummary(active=True, unchanged_matches=7),
            ),
            self.config,
        )

        self.assertIn("No new or price-changed matches", message["content"])
        self.assertIn("Only new or price-changed matches", message["content"])
        self.assertIn("7 unchanged matches", message["content"])

    def test_outcome_status_never_exceeds_the_service_content_limit(self):
        progress = tuple(
            _progress(f"interest {number} " + "x" * 200, 10, number)
            for number in range(20)
        )

        message = build_message(
            build_report([], REPORT_ZONE, interest_progress=progress),
            self.config,
        )

        self.assertLessEqual(len(message["content"]), HIGHEST_CONTENT_LENGTH)
        self.assertTrue(message["content"].endswith("..."))

    def test_a_concerning_tag_colours_the_card_differently(self):
        clear, worrying = _candidates(), _candidates()
        clear[0] = replace(
            clear[0],
            listing=replace(clear[0].listing, grade=read_grade({"condition": "New"})),
        )
        worrying[0] = replace(
            worrying[0],
            listing=replace(
                worrying[0].listing, grade=read_grade({"damage": "Major Damage"})
            ),
        )
        self.assertNotEqual(self._colour(clear), self._colour(worrying))

    def _colour(self, candidates) -> int:
        """The colour of the first card, which is what these tests compare."""
        message = build_message(build_report(candidates, REPORT_ZONE), self.config)
        return message["embeds"][0]["color"]


class AddressTests(unittest.TestCase):
    def setUp(self):
        self.config = WebhookConfig(enabled=True)

    def test_a_missing_address_names_the_variable_to_set(self):
        with patch.dict("os.environ", {"AUCTION_LENS_WEBHOOK_URL": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "AUCTION_LENS_WEBHOOK_URL"):
                webhook_address(self.config)

    def test_an_insecure_address_is_refused(self):
        insecure = {"AUCTION_LENS_WEBHOOK_URL": "http://discord.com/api/webhooks/0/x"}
        with patch.dict("os.environ", insecure, clear=False):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                webhook_address(self.config)

    def test_the_address_is_never_read_from_the_configuration_file(self):
        # It is a password: anyone holding it can post into the channel.
        self.assertNotIn("url", {field for field in vars(self.config) if "env" not in field})

    @patch("auction_lens.reporting.webhook.public_https_opener")
    def test_destination_identity_does_not_post_or_retain_the_webhook(self, opener):
        with patch.dict("os.environ", ENVIRONMENT, clear=False):
            fingerprint = webhook_destination(self.config)

        self.assertEqual(len(fingerprint), 64)
        self.assertNotIn(ADDRESS, fingerprint)
        opener.assert_not_called()

    def test_a_non_public_destination_is_refused(self):
        private = {"AUCTION_LENS_WEBHOOK_URL": "https://127.0.0.1/hook"}
        with patch.dict("os.environ", private, clear=False):
            with self.assertRaisesRegex(ValueError, "non-public"):
                webhook_address(self.config)

    @patch("auction_lens.reporting.webhook.public_https_opener")
    def test_disabled_public_entry_points_never_post(self, opener_factory):
        disabled = WebhookConfig(enabled=False)
        with patch.dict("os.environ", ENVIRONMENT, clear=False):
            for name, action in (
                ("readiness", lambda: check_webhook_ready(disabled)),
                ("identity", lambda: webhook_destination(disabled)),
                ("sender", lambda: send_webhook(build_report([], REPORT_ZONE), disabled)),
            ):
                with self.subTest(action=name):
                    with self.assertRaisesRegex(RuntimeError, "webhook reporting is disabled"):
                        action()
        opener_factory.assert_not_called()


class PostingTests(unittest.TestCase):
    @patch("auction_lens.reporting.webhook.public_https_opener")
    def test_the_default_path_uses_the_redirect_safe_opener(self, opener_factory):
        with patch.dict("os.environ", ENVIRONMENT, clear=False):
            send_webhook(build_report([], REPORT_ZONE), WebhookConfig(enabled=True))

        opener_factory.assert_called_once_with()
        opener_factory.return_value.assert_called_once()

    def test_it_posts_json_to_the_configured_address(self):
        opener = MagicMock()
        with patch.dict("os.environ", ENVIRONMENT, clear=False):
            send_webhook(
                build_report(_candidates(), REPORT_ZONE),
                WebhookConfig(enabled=True),
                opener=opener,
            )

        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, ADDRESS)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Content-type"], "application/json")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["username"], "Auction Lens")
        self.assertTrue(payload["embeds"])

    def test_posted_message_carries_outcome_status(self):
        opener = MagicMock()
        progress = (_progress("soundbar", 1, 1),)
        with patch.dict("os.environ", ENVIRONMENT, clear=False):
            send_webhook(
                build_report([], REPORT_ZONE, interest_progress=progress, unreviewed_wins=1),
                WebhookConfig(enabled=True),
                opener=opener,
            )

        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertIn("soundbar: 1/1 fulfilled; retired", payload["content"])
        self.assertIn("watchlist --verdict won", payload["content"])

    def test_money_survives_as_text_rather_than_a_rounded_float(self):
        opener = MagicMock()
        candidates = _candidates()
        candidates[0] = replace(candidates[0], total_cost=Decimal("20.70"))
        with patch.dict("os.environ", ENVIRONMENT, clear=False):
            send_webhook(
                build_report(candidates, REPORT_ZONE),
                WebhookConfig(enabled=True),
                opener=opener,
            )
        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        costs = [
            field["value"]
            for field in payload["embeds"][0]["fields"]
            if field["name"] == "Cost"
        ]
        self.assertEqual(costs, ["$20.70"])


if __name__ == "__main__":
    unittest.main()
