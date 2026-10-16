"""What this exact product has actually closed at here before.

Every other source in this directory asks somebody else what a thing is worth.
This one asks the only witness with no reason to flatter: the provider's own
past auctions, as this tool recorded them while they ran.

It answers two questions. What has this exact product fetched here before,
and, for a lot whose product has no such record, what did lots at the same
stage of their auction go on to fetch? The second exists because a bid read
hours before the close is an opening price, not a cost: measured on recorded
closes, a lot six to twelve hours out typically finished at 1.8 times the bid
it showed then, and a day out the bid is nearly always the one-dollar opener.

The first exists because stated retail is the seller's own number and cannot be
trusted to rank anything. Measured across 8,491 observed closes, lots led by a
recognisable maker fetched a median 27% of their stated retail while everything
else fetched 15% -- so the claimed figure is inflated hardest exactly where it
is least deserved, and a report ordered by discount against it prefers the
worst goods for the best reason. A number taken from what people really paid
has no such bias, because nobody wrote it down on purpose.

Nothing here fetches anything or writes anything. The database is opened
read-only, which also lets it run safely beside a live run that is recording.
"""

from __future__ import annotations

import re
import sqlite3
from bisect import bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from statistics import median, quantiles

from ..config.pricing import ValuationSourceConfig
from ..listings.model import Listing
from ..values import CENTS, parse_decimal
from .model import ValuationBand, ValuationObservation, ValuationSummary
from .sources import SourceResult, settings_of

# What the band is called on a report card. Not "sold", because a stored bid is
# the last reading before the hammer and not the hammer itself.
BASIS = "observed_close"

# What lots at the same stage went on to close at. Named for where it comes
# from, because a reader comparing it with BASIS has to know it is not this
# product's own record.
STAGE_BASIS = "similar_lots_close"

# A lot's stage is how long it had left and how far its bid had already gone,
# as a share of stated retail. Both sets of edges were tried against recorded
# closes: finer hour bands held too few closes a day out, and the share bands
# separate the one-dollar openers from bids that have started to move.
STAGE_HOURS = (2, 6, 12, 24)
STAGE_SHARES = (
    Decimal("0.02"), Decimal("0.05"), Decimal("0.10"), Decimal("0.20"), Decimal("0.35"),
)
# Fewer lots than this at one stage and its quartiles are mostly noise.
MINIMUM_STAGE_LOTS = 30

# Only a reading taken close to the end describes a closing price. Earlier than
# this, a bid is a lower bound that happens to be the last one recorded.
DEFAULT_ENDPOINT_MINUTES = 30
# One prior close is an anecdote. Two is the fewest that can disagree, which is
# the point of showing a range at all.
DEFAULT_MINIMUM_CLOSES = 2

# Words a warehouse shouts in a title. They describe this copy of the thing, or
# how to collect it, and never which product it is -- so they are removed before
# two titles are compared, and the same grill is one product whether or not
# somebody typed DAMAGED in front of it. What the copy's condition does to its
# price is left to the band, which is wide because that is the truth.
SHOUTING = re.compile(
    r"\*+[^*]*\*+"
    r"|\b(?:stock photo for reference only|truck/trailer pickup only"
    r"|factory sealed|partial set|accessories missing|incomplete)\b",
    re.IGNORECASE,
)
NOT_A_WORD = re.compile(r"[^a-z0-9]+")


def product_key(title: str) -> str:
    """Reduce a title to the identity of the product it is selling.

    Digits are kept deliberately. They are what separate one model from the
    next -- a six-person tent from an eight-person one, a 71-inch ramp from a
    60-inch one -- so discarding them to gain matches would buy coverage by
    quietly comparing different things. Everything dropped is presentation:
    case, punctuation, repeated spaces, and the warehouse's shouting.
    """
    plain = SHOUTING.sub(" ", title).lower()
    return " ".join(NOT_A_WORD.sub(" ", plain).split())


@dataclass(frozen=True)
class Close:
    """One lot's final recorded bid, with the lot's identity kept to exclude it."""

    listing_id: str
    price: object


def observed_ceiling(summary: ValuationSummary) -> Decimal | None:
    """The most this product has ever actually fetched here, if that is known.

    Lives here because this module decides what `observed_close` means, and a
    caller should be able to ask the question without knowing the name of the
    basis or which adapter produced it.

    The answer is in bid units, like every stored close. A caller comparing it
    against a bid needs no conversion, and premium, tax and fee scale both
    sides identically, so the comparison is the same one either way.
    """
    for band in summary.bands:
        if band.basis == BASIS:
            return band.high
    return None


def likely_close(summary: ValuationSummary, current_bid: Decimal) -> ValuationBand | None:
    """Where this lot's bid is likely to finish, in bid units, if anything says.

    The product's own closes come first: tested against recorded closes, they
    landed nearer the final bid than lots at the same stage did at every stage
    more than two hours out. No lot can finish below the bid already placed, so
    every edge is raised to it, and a lot already past its product's whole
    record is expected to close about where it is.
    """
    for basis in (BASIS, STAGE_BASIS):
        for band in summary.bands:
            if band.basis == basis:
                return replace(
                    band,
                    low=max(current_bid, band.low),
                    typical=max(current_bid, band.typical),
                    high=max(current_bid, band.high),
                )
    return None


class ClosingHistoryAdapter:
    """Price a lot from what the same product fetched in this provider's past."""

    def __init__(self, config: ValuationSourceConfig):
        self.config = config
        self.settings = settings_of(config)
        self._closes: dict[str, list[Close]] | None = None
        self._stages: dict[tuple[int, int], tuple[Decimal, ...]] | None = None

    def collect(self, listing: Listing) -> SourceResult:
        """The product's own closes, or failing those, lots at the same stage."""
        prior = [
            close
            for close in self._history().get(product_key(listing.title), ())
            # A lot is never its own comparable, however many times it is seen.
            if close.listing_id != listing.listing_id
        ]
        if len(prior) >= self._minimum_closes():
            return SourceResult(observations=(self._observation(prior),))
        stage = self._stage_observation(listing)
        return SourceResult(observations=(stage,) if stage else ())

    def _observation(self, prior: list[Close]) -> ValuationObservation:
        """State the observed spread, which is reported rather than estimated.

        The low and high are real closes, not a confidence interval. Auction
        prices for one product vary by roughly the width of their own median --
        measured at 84% across every product with three or more closes -- and
        narrowing that to look tidier would be inventing precision. A reader
        comparing a wide honest range against a fabricated retail figure is
        still far better served than by the figure alone.
        """
        prices = sorted(close.price for close in prior)
        # median, not median_low: with an even number of closes the latter hands
        # back the lower of the middle pair, so every two-close answer reported
        # its own floor as the typical price -- a $82 and a $301 close came out
        # as "typical $82". Decimal averages exactly, so the true midpoint is
        # free and the only cost of the wrong one was credibility.
        return ValuationObservation(
            source_id=self.config.source_id,
            basis=BASIS,
            low=prices[0].quantize(CENTS),
            typical=median(prices).quantize(CENTS),
            high=prices[-1].quantize(CENTS),
            sample_size=len(prices),
            notes=(
                f"{len(prices)} previous closes of the same product, "
                f"read within {self._endpoint_minutes()} minutes of ending"
            ),
        )

    def _stage_observation(self, listing: Listing) -> ValuationObservation | None:
        """What lots read at this stage of their auction went on to close at.

        The middle half of those closes, scaled to this lot's stated retail, so
        the range means what it says: tested on closes later than the ones it
        was drawn from, half of all finals landed inside it. A lot with no
        stated retail or no closing time has no stage to compare.
        """
        retail = listing.estimated_retail
        if not retail or listing.ends_at is None:
            return None
        hours = (listing.ends_at - listing.observed_at).total_seconds() / 3600
        if hours < 0:
            return None
        shares = self._stage_closes().get(_stage(hours, listing.current_bid / retail))
        if shares is None:
            return None
        low, typical, high = (
            max(listing.current_bid, retail * share).quantize(CENTS)
            for share in quantiles(shares, n=4)
        )
        return ValuationObservation(
            source_id=self.config.source_id,
            basis=STAGE_BASIS,
            low=low,
            typical=typical,
            high=high,
            sample_size=len(shares),
            notes=(
                f"middle half of {len(shares)} lots read about as long before "
                "closing, with their bid as far along"
            ),
        )

    # ---- settings, each read once per call so a reload is picked up ----------

    def _minimum_closes(self) -> int:
        return self.settings.integer("minimum_closes", DEFAULT_MINIMUM_CLOSES)

    def _endpoint_minutes(self) -> int:
        return self.settings.integer("endpoint_minutes", DEFAULT_ENDPOINT_MINUTES)

    # ---- the history, read once per run rather than once per lot -------------

    def _history(self) -> dict[str, list[Close]]:
        """Load every usable close once; a run asks about thousands of lots."""
        if self._closes is None:
            self._closes = self._read_history()
        return self._closes

    def _read_history(self) -> dict[str, list[Close]]:
        path = Path(self.settings.required_text("path"))
        if not path.is_file():
            return {}
        cutoff = self._endpoint_minutes()
        grouped: dict[str, list[Close]] = {}
        with _read_only(path) as connection:
            for row in connection.execute(_FINAL_READINGS):
                price = _closing_price(row, cutoff)
                if price is None:
                    continue
                key = product_key(row["title"])
                grouped.setdefault(key, []).append(
                    Close(listing_id=str(row["listing_id"]), price=price)
                )
        return grouped

    def _stage_closes(self) -> dict[tuple[int, int], tuple[Decimal, ...]]:
        """Each stage's closes as shares of retail, sorted, read once per run."""
        if self._stages is None:
            self._stages = self._read_stages()
        return self._stages

    def _read_stages(self) -> dict[tuple[int, int], tuple[Decimal, ...]]:
        """Every closed lot, counted once at each stage some pass read it at.

        Once per lot and not once per reading: a lot read five times in one
        afternoon is one auction, and counting it five times would let the lots
        that happened to be watched most closely decide the answer.
        """
        path = Path(self.settings.required_text("path"))
        if not path.is_file():
            return {}
        cutoff = self._endpoint_minutes()
        stages: dict[tuple[int, int], list[Decimal]] = {}
        with _read_only(path) as connection:
            rows = connection.execute(_READINGS_WITH_RETAIL)
            for _, group in groupby(rows, key=lambda row: row["listing_id"]):
                readings = list(group)
                final = _closing_price(readings[-1], cutoff)
                retail = _positive(readings[0]["estimated_retail"])
                ends_at = _moment(readings[0]["ends_at"])
                if final is None or retail is None or ends_at is None:
                    continue
                for stage in _stages_read(readings[:-1], ends_at, retail, cutoff):
                    stages.setdefault(stage, []).append(final / retail)
        return {
            stage: tuple(sorted(shares))
            for stage, shares in stages.items()
            if len(shares) >= MINIMUM_STAGE_LOTS
        }


def _stages_read(
    readings: list[sqlite3.Row], ends_at: datetime, retail: Decimal, cutoff: int
) -> set[tuple[int, int]]:
    """The stages one lot was read at before its close, each named once."""
    stages = set()
    for reading in readings:
        observed_at = _moment(reading["observed_at"])
        if observed_at is None:
            continue
        minutes = (ends_at - observed_at).total_seconds() / 60
        # Inside the endpoint a reading already is the close, not a stage of it.
        if minutes > cutoff:
            bid = _positive(reading["current_bid"]) or Decimal(0)
            stages.add(_stage(minutes / 60, bid / retail))
    return stages


def _stage(hours: float, share: Decimal) -> tuple[int, int]:
    """Which band of time left, and of bid against retail, a reading is in."""
    return bisect_right(STAGE_HOURS, hours), bisect_right(STAGE_SHARES, share)


def _positive(value: object) -> Decimal | None:
    """A stored amount above zero, or None for a blank, a zero or a bad one."""
    try:
        amount = parse_decimal(value, field_name="amount")
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


# Every reading of every lot that states a retail and a closing time, each
# lot's readings together and oldest first, so the last of them is its close.
_READINGS_WITH_RETAIL = """
SELECT l.listing_id       AS listing_id,
       l.estimated_retail AS estimated_retail,
       l.ends_at          AS ends_at,
       h.observed_at      AS observed_at,
       h.current_bid      AS current_bid
FROM listings AS l
JOIN price_history AS h
  ON h.source = l.source AND h.listing_id = l.listing_id
WHERE l.ends_at IS NOT NULL
  AND l.estimated_retail IS NOT NULL
ORDER BY l.source, l.listing_id, h.observed_at
"""


# The last reading of every lot that has an end time, in one pass. Grouping in
# SQL rather than in Python keeps the whole price history out of memory.
_FINAL_READINGS = """
SELECT l.listing_id    AS listing_id,
       l.title         AS title,
       l.ends_at       AS ends_at,
       h.observed_at   AS observed_at,
       h.current_bid   AS current_bid
FROM listings AS l
JOIN price_history AS h
  ON h.source = l.source AND h.listing_id = l.listing_id
JOIN (
    SELECT source, listing_id, MAX(observed_at) AS final
    FROM price_history
    GROUP BY source, listing_id
) AS last
  ON last.source = h.source
 AND last.listing_id = h.listing_id
 AND last.final = h.observed_at
WHERE l.ends_at IS NOT NULL
"""


def _closing_price(row: sqlite3.Row, endpoint_minutes: int):
    """The final bid, but only when it was read late enough to mean the close."""
    ends_at = _moment(row["ends_at"])
    observed_at = _moment(row["observed_at"])
    if ends_at is None or observed_at is None:
        return None
    minutes_early = (ends_at - observed_at).total_seconds() / 60
    if not 0 <= minutes_early <= endpoint_minutes:
        return None
    try:
        price = parse_decimal(row["current_bid"], field_name="current_bid")
    except ValueError:
        return None
    # A lot nobody bid on says nothing about what the product fetches.
    return price if price > 0 else None


def _moment(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


@contextmanager
def _read_only(path: Path) -> Iterator[sqlite3.Connection]:
    """Open the history so that no mistake here can alter it, and close it.

    Closing has to be explicit. A sqlite3 connection used as a context manager
    commits or rolls back a transaction and leaves the connection open, so the
    obvious `with sqlite3.connect(...)` would hold a read handle on the live
    database for the whole run.
    """
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        connection.close()
