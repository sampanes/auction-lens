"""What this exact product has actually closed at here before.

Every other source in this directory asks somebody else what a thing is worth.
This one asks the only witness with no reason to flatter: the provider's own
past auctions, as this tool recorded them while they ran.

It exists because stated retail is the seller's own number and cannot be
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

from ..config.pricing import ValuationSourceConfig
from ..listings.model import Listing
from ..values import CENTS, parse_decimal
from .model import ValuationObservation
from .sources import SourceResult, settings_of

# What the band is called on a report card. Not "sold", because a stored bid is
# the last reading before the hammer and not the hammer itself.
BASIS = "observed_close"

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


class ClosingHistoryAdapter:
    """Price a lot from what the same product fetched in this provider's past."""

    def __init__(self, config: ValuationSourceConfig):
        self.config = config
        self.settings = settings_of(config)
        self._closes: dict[str, list[Close]] | None = None

    def collect(self, listing: Listing) -> SourceResult:
        """Answer only where the same product has closed often enough before."""
        prior = [
            close
            for close in self._history().get(product_key(listing.title), ())
            # A lot is never its own comparable, however many times it is seen.
            if close.listing_id != listing.listing_id
        ]
        if len(prior) < self._minimum_closes():
            return SourceResult()
        return SourceResult(observations=(self._observation(prior),))

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
