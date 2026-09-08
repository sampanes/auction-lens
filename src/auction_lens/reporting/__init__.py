"""Deciding what a report says, rendering it, and delivering it."""

from .delivery import check_email_ready, send_email, send_watchlist_email
from .findings import Report, build_report
from .html import render_html
from .text import render_text
from .watchlist import render_watchlist, render_watchlist_html
from .webhook import send_webhook

__all__ = [
    "Report",
    "build_report",
    "check_email_ready",
    "render_html",
    "render_text",
    "render_watchlist",
    "render_watchlist_html",
    "send_email",
    "send_watchlist_email",
    "send_webhook",
]
