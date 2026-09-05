"""Support ``python -m auction_lens`` alongside the installed script."""

from __future__ import annotations

from .cli import main

raise SystemExit(main())
