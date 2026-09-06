"""Support ``python -m auction_lens`` alongside the installed script."""

from __future__ import annotations

from .cli import console

raise SystemExit(console())
