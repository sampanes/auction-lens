"""Posting a report to a chat webhook: what it says, and what it refuses to do."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from auction_lens.config import WebhookConfig
from auction_lens.grading import read_grade
from auction_lens.reporting import send_webhook
from auction_lens.reporting.webhook import (
    HIGHEST_EMBED_COUNT,
    build_message,
    webhook_address,
)
from auction_lens.scoring import evaluate
from support import SOUNDBAR, example_config, example_listings

ADDRESS = "https://discord.com/api/webhooks/000/secret-token"
ENVIRONMENT = {"AUCTION_LENS_WEBHOOK_URL": ADDRESS}


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
        message = build_message(_candidates(), self.config)
        self.assertEqual(message["embeds"][0]["url"], _candidates()[0].listing.url)

    def test_it_never_sends_more_cards_than_the_service_accepts(self):
        message = build_message(_candidates(40), replace(self.config, max_items=99))
        self.assertEqual(len(message["embeds"]), HIGHEST_EMBED_COUNT)
        self.assertIn("the best 10 follow", message["content"])

    def test_a_smaller_limit_is_respected(self):
        message = build_message(_candidates(40), replace(self.config, max_items=3))
        self.assertEqual(len(message["embeds"]), 3)

    def test_finding_nothing_still_says_so(self):
        message = build_message([], self.config)
        self.assertEqual(message["embeds"], [])
        self.assertIn("Nothing matched", message["content"])

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
        self.assertNotEqual(
            build_message(clear, self.config)["embeds"][0]["color"],
            build_message(worrying, self.config)["embeds"][0]["color"],
        )


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


class PostingTests(unittest.TestCase):
    @patch("auction_lens.reporting.webhook.urlopen")
    def test_it_posts_json_to_the_configured_address(self, urlopen):
        with patch.dict("os.environ", ENVIRONMENT, clear=False):
            send_webhook(_candidates(), WebhookConfig(enabled=True))

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, ADDRESS)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Content-type"], "application/json")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["username"], "Auction Lens")
        self.assertTrue(payload["embeds"])

    @patch("auction_lens.reporting.webhook.urlopen")
    def test_money_survives_as_text_rather_than_a_rounded_float(self, urlopen):
        candidates = _candidates()
        candidates[0] = replace(candidates[0], total_cost=Decimal("20.70"))
        with patch.dict("os.environ", ENVIRONMENT, clear=False):
            send_webhook(candidates, WebhookConfig(enabled=True))
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        costs = [
            field["value"]
            for field in payload["embeds"][0]["fields"]
            if field["name"] == "Cost"
        ]
        self.assertEqual(costs, ["$20.70"])


if __name__ == "__main__":
    unittest.main()
