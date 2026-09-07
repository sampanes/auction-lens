"""Posting a report to a chat webhook, for the times you want it now.

Email is the scheduled digest: it arrives whether or not anybody asked. A
webhook is the opposite errand -- somebody ran the command and wants the answer
on their phone within seconds -- so this stays deliberately small and sends one
message rather than a document.

The address is a secret and is read from the environment, never from the
configuration file. Anyone holding it can post into the channel, so it belongs
with the passwords rather than with the preferences.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

from ..config import WebhookConfig
from ..grading import Tag
from ..models import Candidate

WEBHOOK_TIMEOUT_SECONDS = 15

# Discord accepts at most ten embeds in one message, and refuses the whole
# message if there are more, so this is a hard limit rather than a preference.
HIGHEST_EMBED_COUNT = 10
HIGHEST_TITLE_LENGTH = 256

# The colours the watchlist already uses, as the integers a webhook wants.
COLOURS = {Tag.GREEN: 0x2E7D32, Tag.AMBER: 0xF9A825, Tag.RED: 0xC62828}
ALL_CLEAR = "every tag green"


def send_webhook(candidates: list[Candidate], config: WebhookConfig) -> None:
    """Post the best candidates to the configured chat webhook."""
    address = webhook_address(config)
    payload = build_message(candidates, config)
    request = Request(
        address,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
        response.read()


def webhook_address(config: WebhookConfig) -> str:
    """Read the secret address, and say plainly which variable is missing."""
    address = os.getenv(config.url_env, "").strip()
    if not address:
        raise RuntimeError(f"{config.url_env} must contain the webhook address")
    if not address.startswith("https://"):
        raise ValueError("the webhook address must be HTTPS")
    return address


def build_message(candidates: list[Candidate], config: WebhookConfig) -> dict[str, Any]:
    """One message: a line saying how many, then a card for each of the best.

    Public because it is worth testing without posting anything anywhere.
    """
    shown = candidates[: min(config.max_items, HIGHEST_EMBED_COUNT)]
    return {
        "username": config.username,
        "content": _headline(len(candidates), len(shown)),
        "embeds": [_card(candidate) for candidate in shown],
    }


def _headline(found: int, shown: int) -> str:
    if not found:
        return "Nothing matched this run."
    if shown < found:
        return f"{found} matches; the best {shown} follow."
    return f"{found} match(es)."


def _card(candidate: Candidate) -> dict[str, Any]:
    """One lot, with its address on the title so a tap opens the listing.

    A provider that publishes app links serves that same address into its own
    app on a phone, so no second, app-flavoured address is needed here.
    """
    listing = candidate.listing
    return {
        "title": listing.title[:HIGHEST_TITLE_LENGTH],
        "url": listing.url,
        "color": COLOURS[_worst_tag(candidate)],
        "fields": [
            {"name": "Cost", "value": f"${candidate.total_cost}", "inline": True},
            {"name": "Retail", "value": _retail(candidate), "inline": True},
            {"name": "Where", "value": listing.location or "unstated", "inline": True},
            {"name": "Condition", "value": _conditions(candidate), "inline": False},
            {"name": "Why", "value": ", ".join(candidate.reasons) or "-", "inline": False},
        ],
    }


def _worst_tag(candidate: Candidate) -> Tag:
    """Colour the card by the most concerning thing the provider admitted to."""
    grade = candidate.listing.grade
    tags = {tag.tag for tag in grade.tags} if grade else set()
    for shade in (Tag.RED, Tag.AMBER):
        if shade in tags:
            return shade
    return Tag.GREEN


def _retail(candidate: Candidate) -> str:
    retail = candidate.listing.estimated_retail
    if retail is None:
        return "unstated"
    if candidate.retail_ratio is None:
        return f"${retail}"
    return f"${retail} ({candidate.retail_ratio:.0%})"


def _conditions(candidate: Candidate) -> str:
    grade = candidate.listing.grade
    if grade is None or not grade.tags:
        return "not stated"
    return ", ".join(tag.label for tag in grade.concerns) or ALL_CLEAR
