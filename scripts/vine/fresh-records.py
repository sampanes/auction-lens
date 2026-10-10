"""Stage recent companion records, or exit 1 when there are none.

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
records answer it themselves. Only those recent rows are written to the staged
file, so one new row cannot carry older sightings into the morning report.
`providers/vine/swept.py` holds the whole reason.

Usage: python scripts/vine/fresh-records.py --output private/vine-fresh.ndjson
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from pathlib import Path

from auction_lens.files import write_bytes_atomically
from auction_lens.providers.vine.swept import (
    CLOCK_TOLERANCE,
    MAIL_WINDOW_HOURS,
    describe_age,
    is_recent,
    moment_from,
    now_utc,
)

ROOT = Path(__file__).resolve().parent.parent.parent
RECORD_DIR = ROOT / "private" / "vine"

# The sink stamps each row with the moment it appended it, which is the one
# field here that means "when did this arrive" rather than "when did a page
# claim to have been seen". A page's own clock is not the reader's.
ARRIVAL_FIELD = "received_at"


def record_rows(record_dir: Path | None = None) -> Iterator[dict[str, object]]:
    """Every readable object in the companion's record files.

    One corrupt line or unreadable file cannot hide a day that otherwise
    collected. Rows with unreadable arrival stamps remain visible to the
    freshness decision, which declines them rather than guessing their age.
    """
    directory = record_dir if record_dir is not None else RECORD_DIR
    try:
        files = sorted(directory.glob("queue-*.ndjson"))
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
                yield row


def fresh_rows(
    rows: Iterable[dict[str, object]], now: datetime
) -> list[dict[str, object]]:
    """Rows inside the mail window, newest first for first-wins deduplication."""
    accepted: list[tuple[datetime, dict[str, object]]] = []
    for row in rows:
        moment = moment_from(row.get(ARRIVAL_FIELD))
        if moment is not None and is_recent(moment, now):
            accepted.append((moment, row))
    accepted.sort(key=lambda found: found[0], reverse=True)
    return [row for _moment, row in accepted]


def _newest_usable(
    rows: Iterable[dict[str, object]], now: datetime
) -> datetime | None:
    """Newest readable arrival no further ahead than ordinary clock skew."""
    newest = None
    for row in rows:
        moment = moment_from(row.get(ARRIVAL_FIELD))
        if moment is None or moment - now > CLOCK_TOLERANCE:
            continue
        if newest is None or moment > newest:
            newest = moment
    return newest


def stage_fresh_records(
    output: Path,
    *,
    now: datetime | None = None,
    record_dir: Path | None = None,
) -> tuple[int, datetime | None]:
    """Atomically replace the stage with recent rows and return count/newest."""
    measured_at = now if now is not None else now_utc()
    rows = list(record_rows(record_dir))
    fresh = fresh_rows(rows, measured_at)
    newest = _newest_usable(fresh if fresh else rows, measured_at)
    contents = "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in fresh
    ).encode("utf-8")
    # Replace even with an empty file. A failed gate must not leave yesterday's
    # stage looking usable to a person debugging the next morning's run.
    write_bytes_atomically(output, contents)
    return len(fresh), newest


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage only Vine companion records fresh enough to mail"
    )
    parser.add_argument("--output", required=True, help="fresh NDJSON staging file")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Stage fresh records and report whether the stage contains anything."""
    args = _arguments(argv)
    now = now_utc()
    count, newest = stage_fresh_records(Path(args.output), now=now)
    age = describe_age(newest, now)
    if count == 0:
        print(
            f"[!] nothing recorded in the last {MAIL_WINDOW_HOURS}h; "
            f"newest usable is {age}"
        )
        return 1
    print(
        f"[OK] staged {count} record(s) from the last {MAIL_WINDOW_HOURS}h; "
        f"newest is {age}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
