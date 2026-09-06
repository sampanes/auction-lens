"""Deciding what a report says, rendering it, and delivering it."""

from .delivery import send_email, send_watchlist_email
from .findings import Report, build_report
from .html import render_html
from .text import render_text
from .watchlist import render_watchlist, render_watchlist_html

__all__ = [
    "Report",
    "build_report",
    "render_html",
    "render_text",
    "render_watchlist",
    "render_watchlist_html",
    "send_email",
    "send_watchlist_email",
]
