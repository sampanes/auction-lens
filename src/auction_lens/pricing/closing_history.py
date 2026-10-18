"""What this exact product has actually closed at here before.

Every other source in this directory asks somebody else what a thing is worth.
This one asks the only witness with no reason to flatter: the provider's own
past auctions, as this tool recorded them while they ran.

It answers one question: what has this exact product fetched here before. A
lot whose product has no such record gets no answer, and the report says so.

Scaling stated retail by what lots at the same stage fetched was tried here and
removed. The figure it produced was the stated retail rescaled by one constant
per stage, so it read as evidence while carrying none: measured over 16,965
closes, the share of retail a lot ends at is a curve decaying from zero, with a
16.5% median, a 19.7% mean, and only 2.1% of lots inside a point of that mean.
No single multiple of retail describes a lot, and a card is better off silent.

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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from ..config.pricing import ValuationSourceConfig
from ..listings.model import Listing
from ..values import CENTS, parse_decimal
from .model import ValuationBand, ValuationObservation, ValuationSummary
from .sources import SourceResult, settings_of

# What the band is called on a report card. Not "sold", because a stored bid is
# the last reading before the hammer and not the hammer itself.
BASIS = "observed_close"

# What the same product really sold for, read from each lot's own page once it
# had shut. This is the hammer, so it is named for that and kept apart from the
# reading above: the two are both observations and only one of them is a sale.
# A product with enough of these is priced from them alone, because mixing a
# sale with a bid recorded an hour early would average away the better answer.
SETTLED_BASIS = "settled_close"

# Which answer wins where a product has both, best first.
BASIS_PREFERENCE = (SETTLED_BASIS, BASIS)

# Only a reading taken close to the end describes a closing price. Earlier than
# this, a bid is a lower bound that happens to be the last one recorded.
#
# Ninety minutes, because this is now the only bar deciding whether a lot has an
# answer at all, and thirty left almost every card without one. The passes are
# an hour or more apart, so just 14% of recorded lots were read inside thirty
# minutes of closing, and only 2,059 products reached the two closes a range
# needs; at ninety, 9,366 do. What that costs is small and measured: across 562
# lots read both in the 31-to-90-minute band and again in the final half hour,
# the bid gained a median $0.00, 55% of them did not move at all, and the
# ninetieth percentile gained $15. A provider read less often than this should
# raise it, and one polled every few minutes should lower it.
DEFAULT_ENDPOINT_MINUTES = 90
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
    for basis in BASIS_PREFERENCE:
        for band in summary.bands:
            if band.basis == basis:
                return band.high
    return None


def likely_close(summary: ValuationSummary, current_bid: Decimal) -> ValuationBand | None:
    """Where this lot's bid is likely to finish, in bid units, if anything says.

    Only this product's own record answers, a real sale ahead of a reading
    taken before the close. Nothing derived from stated retail answers at all,
    because that number is the seller's and a constant multiple of it forecasts
    nothing, so a lot with no record gets no band. No lot can finish below the
    bid already placed, so every edge is raised to it, and a lot already past
    its product's whole record is expected to close about where it is.
    """
    for basis in BASIS_PREFERENCE:
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
        self._sales: dict[str, list[Close]] | None = None

    def collect(self, listing: Listing) -> SourceResult:
        """What this product really sold for, else what it was last read at.

        A sale is preferred outright rather than pooled with readings. Both are
        observations, but only the sale is the price somebody actually paid,
        and a reading recorded an hour before the end is a floor: averaging the
        two would pull the better answer down by exactly the error that made
        reading-based prices worth replacing.
        """
        key = product_key(listing.title)
        wanted = self._minimum_closes()
        sales = self._own(self._sold().get(key, ()), listing)
        if len(sales) >= wanted:
            return SourceResult(
                observations=(self._observation(sales, basis=SETTLED_BASIS),)
            )
        prior = self._own(self._history().get(key, ()), listing)
        if len(prior) < wanted:
            return SourceResult(observations=())
        return SourceResult(observations=(self._observation(prior, basis=BASIS),))

    @staticmethod
    def _own(closes, listing: Listing) -> list[Close]:
        """Every close but this lot's: it is never its own comparable."""
        return [close for close in closes if close.listing_id != listing.listing_id]

    def _observation(
        self, prior: list[Close], *, basis: str = BASIS
    ) -> ValuationObservation:
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
        settled = basis == SETTLED_BASIS
        return ValuationObservation(
            source_id=self.config.source_id,
            basis=basis,
            low=prices[0].quantize(CENTS),
            typical=median(prices).quantize(CENTS),
            high=prices[-1].quantize(CENTS),
            sample_size=len(prices),
            notes=(
                f"{len(prices)} recorded sales of the same product"
                if settled
                else f"{len(prices)} previous closes of the same product, "
                f"read within {self._endpoint_minutes()} minutes of ending"
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

    def _sold(self) -> dict[str, list[Close]]:
        """Load every recorded sale once, for the same reason as the above."""
        if self._sales is None:
            self._sales = self._read_sales()
        return self._sales

    def _read_sales(self) -> dict[str, list[Close]]:
        """Group recorded sales by product.

        No endpoint window applies. A settling pass reads a lot after it has
        shut, so the price it stores is the result and not a reading that has
        to be argued about -- which is the whole reason the table exists.
        """
        path = Path(self.settings.required_text("path"))
        if not path.is_file():
            return {}
        grouped: dict[str, list[Close]] = {}
        with _read_only(path) as connection:
            try:
                rows = connection.execute(_SETTLED_SALES).fetchall()
            except sqlite3.DatabaseError:
                # A database written before settling existed has no such table.
                # Missing history is a normal state here, not a failure.
                return {}
            for row in rows:
                price = _positive_price(row["final_bid"])
                if price is None:
                    continue
                grouped.setdefault(product_key(row["title"]), []).append(
                    Close(listing_id=str(row["listing_id"]), price=price)
                )
        return grouped

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

# Every recorded sale, with the title it sold under so two lots of the same
# product group together.
_SETTLED_SALES = """
SELECT s.listing_id AS listing_id,
       s.final_bid   AS final_bid,
       l.title       AS title
FROM settled_closes AS s
JOIN listings AS l
  ON l.source = s.source AND l.listing_id = s.listing_id
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


def _positive_price(value: object) -> Decimal | None:
    """A stored sale price, or None for a blank or unreadable one.

    Zero is kept. A lot really can close with no bid at all, and that is a fact
    about the product worth having rather than a missing value.
    """
    try:
        amount = parse_decimal(value, field_name="final_bid")
    except (TypeError, ValueError):
        return None
    return amount if amount >= 0 else None
