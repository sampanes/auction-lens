"""Rendering findings for a person, and delivering them."""

from .delivery import send_email
from .html import render_html
from .text import render_text

__all__ = ["render_html", "render_text", "send_email"]
