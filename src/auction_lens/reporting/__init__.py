"""Deciding what a report says, rendering it, and delivering it."""

from .delivery import (
    check_email_ready,
    email_destination,
    send_email,
    send_watchlist_email,
)
from .destinations import destination_fingerprint
from .findings import (
    DeliverySummary,
    OutcomeSummary,
    Report,
    build_outcome_summary,
    build_report,
)
from .html import render_html
from .searches import SearchHint, search_hints
from .text import render_text
from .watchlist import render_watchlist, render_watchlist_html
from .webhook import check_webhook_ready, send_webhook, webhook_destination

__all__ = [
    "Report",
    "DeliverySummary",
    "OutcomeSummary",
    "build_outcome_summary",
    "build_report",
    "SearchHint",
    "search_hints",
    "check_email_ready",
    "check_webhook_ready",
    "destination_fingerprint",
    "email_destination",
    "render_html",
    "render_text",
    "render_watchlist",
    "render_watchlist_html",
    "send_email",
    "send_watchlist_email",
    "send_webhook",
    "webhook_destination",
]
