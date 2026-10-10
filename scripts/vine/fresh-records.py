"""Exit 0 if the companion has recorded something recently, 1 if it has not.

An exit code rather than printed text on purpose. The scheduled runner is a
.cmd file, and capturing a program's output there means nesting quotes inside
a `for /f` command: the first version of this got that wrong, both captured
values came out empty, the comparison of one empty string to another compared
equal, and the gate let a run through on the one morning it existed to stop.

An exit code cannot be misquoted, and every failure here -- unreadable row,
missing directory, missing timestamp, broken interpreter -- lands on a non-zero
code, which is the direction a gate should fail in.

Two earlier versions of this gate asked the wrong question and each silenced
the mail completely. The first compared the sweep's date with today's, and a
sweep is an evening activity while this runs in the morning, so the freshest
records there are were always rejected. The second asked whether a sweep had
*finished*, and a sweep is not reliably allowed to finish -- Amazon throttles
it after a term or two and it abandons the attempt -- so a night that recorded
hundreds of rows across fifteen searches still reported nothing to send.

So this asks the reader's actual question: has anything arrived lately. The
records answer it themselves. `providers/vine/swept.py` holds the whole reason.

Usage: python scripts/vine/fresh-records.py
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from auction_lens.providers.vine.swept import (
    MAIL_WINDOW_HOURS,
    describe_age,
    is_recent,
    newest_moment,
    now_utc,
)

ROOT = Path(__file__).resolve().parent.parent.parent
RECORD_DIR = ROOT / "private" / "vine"

# The sink stamps each row with the moment it appended it, which is the one
# field here that means "when did this arrive" rather than "when did a page
# claim to have been seen". A page's own clock is not the reader's.
ARRIVAL_FIELD = "received_at"


def arrival_times() -> Iterator[str]:
    """Every arrival stamp in the companion's records, readable or not.

    Unreadable lines are skipped here and unreadable stamps are skipped by the
    caller, so one corrupt line cannot hide a day that otherwise collected.
    """
    try:
        files = sorted(RECORD_DIR.glob("queue-*.ndjson"))
    except OSError:
        return
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row.get(ARRIVAL_FIELD)


def main() -> int:
    """Report whether anything arrived inside the window, as an exit code."""
    now = now_utc()
    newest = newest_moment(arrival_times())
    age = describe_age(newest, now)
    if not is_recent(newest, now):
        print(f"[!] nothing recorded in the last {MAIL_WINDOW_HOURS}h; newest is {age}")
        return 1
    print(
        f"[OK] the companion recorded something {age}, "
        f"inside the {MAIL_WINDOW_HOURS}h window"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
