"""Print every logged Vine sweep beside the one before it.

The evidence for one claim about Vine: that items arrive around midnight and
only thin out through the day. Each line is one sweep -- when, which slot, how
many items it saw, how many no record had seen before it began, and how many
of the last sweep's items it no longer found. If evening sweeps keep finding
items the morning did not, the claim is wrong; if they only find fewer, it
holds. `providers/vine/pattern.py` says what counts as new and as gone.

Usage: python scripts/vine/pattern.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from auction_lens.providers.vine.pattern import (
    describe,
    logged_sweeps,
    readings,
    record_rows,
)

ROOT = Path(__file__).resolve().parent.parent.parent
RECORD_DIR = ROOT / "private" / "vine"
SWEEP_LOG = RECORD_DIR / "sweeps.ndjson"


def main() -> int:
    """Print one line per sweep, oldest first."""
    found = readings(record_rows(RECORD_DIR), logged_sweeps(SWEEP_LOG))
    if not found:
        print(f"[!] no sweeps logged yet in {SWEEP_LOG}")
        return 1
    zone = datetime.now().astimezone().tzinfo
    for reading in found:
        print(describe(reading, zone))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
