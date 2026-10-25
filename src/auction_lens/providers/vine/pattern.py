"""What each sweep saw, compared with what came before it.

The question this answers is the operator's theory about Vine: that items
arrive around midnight and only thin out through the day. A single sweep cannot
say anything about that. Two can, if they are compared fairly, and that is the
whole difficulty here.

"New" is the easy half. An item is new to a sweep when no record of any kind,
swept or browsed, saw it before that sweep began.

"Gone" is where an unfair comparison would lie. A sweep reads the first page of
each term, and a term with more results than one page holds can push an item
off that page without it leaving Vine. So an item counts as gone only from a
term whose results fitted on one page both times, and the number of such terms
is reported beside it. A term with no results at all posts no rows, which is
why the sweep's own account of what it searched comes from the sweep log and
not from the records: without it, "searched and empty" and "never searched"
would read the same.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .swept import moment_from

SWEEP_FIELD = "sweep"
ARRIVAL_FIELD = "received_at"
RECORD_PATTERN = "queue-*.ndjson"


def record_rows(directory: Path) -> Iterator[dict[str, Any]]:
    """Every readable object in the companion's record files.

    One corrupt line or unreadable file cannot hide a day that otherwise
    collected; it is skipped, and the rest of the day is still read.
    """
    try:
        files = sorted(directory.glob(RECORD_PATTERN))
    except OSError:
        return
    for path in files:
        yield from _objects(path)


def logged_sweeps(path: Path) -> Iterator[dict[str, Any]]:
    """Every readable line of the sink's sweep log."""
    yield from _objects(path)


def _objects(path: Path) -> Iterator[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


@dataclass(frozen=True)
class SweepReading:
    """One sweep, and how it differs from the sweep before it."""

    sweep: str
    slot: str
    started: datetime
    searched: int
    seen: int
    new: int
    # None for the first sweep, which has nothing to be compared with.
    gone: int | None
    comparable_terms: int


def readings(
    rows: Iterable[Mapping[str, Any]], sweeps: Iterable[Mapping[str, Any]]
) -> list[SweepReading]:
    """Every logged sweep, oldest first, measured against its predecessor."""
    first_seen: dict[str, datetime] = {}
    by_sweep: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    deep_terms: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        asin = str(row.get("asin") or "")
        moment = moment_from(row.get(ARRIVAL_FIELD))
        if not asin or moment is None:
            continue
        if asin not in first_seen or moment < first_seen[asin]:
            first_seen[asin] = moment
        sweep = row.get(SWEEP_FIELD)
        if not isinstance(sweep, str) or not sweep:
            continue
        term = str(row.get("search_term") or "")
        by_sweep[sweep][term].add(asin)
        if _more_than_one_page(row.get("pages_available")):
            deep_terms[sweep].add(term)

    logged = sorted(
        (entry for entry in (_logged(entry) for entry in sweeps) if entry is not None),
        key=lambda entry: entry[1],
    )
    found: list[SweepReading] = []
    previous: tuple[str, set[str]] | None = None
    for sweep, started, slot, searched in logged:
        terms = by_sweep.get(sweep, {})
        asins = set().union(*terms.values()) if terms else set()
        new = sum(1 for asin in asins if first_seen.get(asin, started) >= started)
        gone = None
        comparable = 0
        if previous is not None:
            before, before_searched = previous
            fair = {
                term
                for term in searched & before_searched
                if term not in deep_terms[sweep] and term not in deep_terms[before]
            }
            comparable = len(fair)
            earlier = by_sweep.get(before, {})
            gone = len(
                set().union(*(earlier.get(term, set()) for term in fair))
                - set().union(*(terms.get(term, set()) for term in fair))
            ) if fair else 0
        found.append(
            SweepReading(sweep, slot, started, len(searched), len(asins), new, gone, comparable)
        )
        previous = (sweep, searched)
    return found


def _logged(entry: Mapping[str, Any]) -> tuple[str, datetime, str, set[str]] | None:
    """One sweep log line, or None when it cannot be placed in time."""
    sweep = entry.get(SWEEP_FIELD)
    started = moment_from(entry.get("started_at"))
    if not isinstance(sweep, str) or not sweep or started is None:
        return None
    searched = entry.get("searched_terms")
    terms = {str(term) for term in searched} if isinstance(searched, list) else set()
    return sweep, started, str(entry.get("slot") or ""), terms


def _more_than_one_page(value: Any) -> bool:
    return isinstance(value, int) and value > 1


def describe(reading: SweepReading, zone) -> str:
    """One line a person can read down a column of."""
    local = reading.started.astimezone(zone).strftime("%a %m/%d %H:%M")
    gone = (
        "first sweep"
        if reading.gone is None
        else f"{reading.gone} gone from {reading.comparable_terms} comparable term(s)"
    )
    return (
        f"{local}  {reading.slot or 'by hand':8} {reading.searched:3} searched  "
        f"{reading.seen:4} seen  {reading.new:4} new  {gone}"
    )
