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
from collections.abc import Callable
from typing import Any
from urllib.request import Request
from zoneinfo import ZoneInfo

from ..config import WebhookConfig
from ..grading import Tag
from ..http_safety import public_https_opener, require_public_https
from ..models import Candidate, Listing, ranked
from .destinations import destination_fingerprint
from .findings import (
    DeliverySummary,
    OutcomeSummary,
    Report,
    closing_time,
)

WEBHOOK_TIMEOUT_SECONDS = 15

# Discord accepts at most ten embeds in one message, and refuses the whole
# message if there are more, so this is a hard limit rather than a preference.
HIGHEST_EMBED_COUNT = 10
HIGHEST_TITLE_LENGTH = 256
HIGHEST_CONTENT_LENGTH = 2_000

# The colours the watchlist already uses, as the integers a webhook wants.
COLOURS = {Tag.GREEN: 0x2E7D32, Tag.AMBER: 0xF9A825, Tag.RED: 0xC62828}
ALL_CLEAR = "every tag green"


def send_webhook(
    report: Report,
    config: WebhookConfig,
    *,
    opener: Callable[..., Any] | None = None,
) -> None:
    """Post the best of a built report to the configured chat webhook."""
    address = _ready_address(config)
    payload = build_message(report, config)
    request = Request(
        address,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    open_request = public_https_opener() if opener is None else opener
    with open_request(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
        response.read()


def webhook_address(config: WebhookConfig) -> str:
    """Read the secret address, and say plainly which variable is missing."""
    address = os.getenv(config.url_env, "").strip()
    if not address:
        raise RuntimeError(f"{config.url_env} must contain the webhook address")
    require_public_https(address)
    return address


def webhook_destination(config: WebhookConfig) -> str:
    """An opaque identity for the resolved webhook this run would contact."""
    return destination_fingerprint(_ready_address(config))


def check_webhook_ready(config: WebhookConfig) -> None:
    """Validate local webhook settings without connecting or posting anything."""
    _ready_address(config)


def _ready_address(config: WebhookConfig) -> str:
    if not config.enabled:
        raise RuntimeError("webhook reporting is disabled in the selected configuration")
    return webhook_address(config)


def webhook_item_limit(config: WebhookConfig, report_limit: int | None) -> int:
    """The exact number of cards the transport can accept from one report."""
    limits = [config.max_items, HIGHEST_EMBED_COUNT]
    if report_limit is not None:
        limits.append(report_limit)
    return min(limits)


def build_message(report: Report, config: WebhookConfig) -> dict[str, Any]:
    """One message: a line saying how many, then a card for each of the best.

    Chat has a harder length limit than mail, so this takes its own last cut
    from the same ranking rather than rendering the report's worded groups.

    Public because it is worth testing without posting anything anywhere.
    """
    candidates = list(report.candidates)
    selected = ranked(candidates, limit=webhook_item_limit(config, None))
    shown = ranked(selected, order=report.order)
    return {
        "username": config.username,
        "content": _content(
            len(candidates), len(shown), report.outcomes, report.delivery
        ),
        "embeds": [_card(candidate, report.zone) for candidate in shown],
    }


def _content(
    found: int,
    shown: int,
    outcomes: OutcomeSummary,
    delivery: DeliverySummary,
) -> str:
    """Add outcome context without letting Discord reject an oversized post."""
    lines = [_headline(found, shown, delivery)]
    lines.extend(delivery.lines)
    if outcomes.warning:
        lines.append(outcomes.warning)
    if outcomes.progress:
        lines.append("Interests: " + " | ".join(outcomes.progress))
    content = "\n".join(lines)
    if len(content) <= HIGHEST_CONTENT_LENGTH:
        return content
    return content[: HIGHEST_CONTENT_LENGTH - 3].rstrip() + "..."


def _headline(found: int, shown: int, delivery: DeliverySummary) -> str:
    if not found:
        if delivery.active and not delivery.repeated:
            return "No new or price-changed matches for this destination."
        return "Nothing matched this run."
    if shown < found:
        return f"{found} matches; the best {shown} follow."
    return f"{found} match(es)."


def _card(candidate: Candidate, zone: ZoneInfo) -> dict[str, Any]:
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
            {"name": "Closes", "value": _closes(listing, zone), "inline": True},
            {"name": "Condition", "value": _conditions(candidate), "inline": False},
            {"name": "Why", "value": ", ".join(candidate.reasons) or "-", "inline": False},
            {
                "name": "Watch key",
                "value": f"{listing.source}/{listing.listing_id}",
                "inline": False,
            },
        ],
    }


def _closes(listing: Listing, zone: ZoneInfo) -> str:
    """Worded by the same function the mailed report uses, so the two agree.

    A chat card has a fixed set of fields where the mailed report can simply
    leave a fact out, so an unstated time is said rather than omitted.
    """
    return closing_time(listing.ends_at, zone) or "unstated"


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
