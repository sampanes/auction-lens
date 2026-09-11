"""What a person thinks of a lot, and every look they have taken at its price.

This is the half of the project no run may overwrite. A scored candidate is the
tool's opinion and is rebuilt every morning; these records are the operator's,
and they accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ..fields import require_not_negative
from ..grading import ConditionTag
from .interests import InterestRef, _unique_interest_refs
from .lots import key_of


class Verdict(StrEnum):
    """What a person has decided about a lot they are following.

    This is the person's own word, and is nothing to do with the provider's
    condition tags. Those live in ``grading`` and are the lot's, not theirs.
    """

    # Declared in the order a person reads them: what is being chased first,
    # what was decided against last. Sorting reads this order and nothing else.
    HUNTING = "hunting"
    WATCHING = "watching"
    WON = "won"
    LOST = "lost"
    PASSED = "passed"


@dataclass(frozen=True)
class PriceReading:
    """One look at a lot: what it cost at that moment.

    A scan every hour leaves twenty-four of these in a day; a scan once leaves
    one. That is the whole point of keeping them as a list rather than as a
    single "current price" that forgets everything it replaces.

    The auction is recorded on the reading rather than on the item, because a
    trail that spans a relisting has readings from more than one auction.
    """

    scanned_at: datetime
    current_bid: Decimal
    total_cost: Decimal
    bid_count: int = 0
    listing_id: str = ""

    def __post_init__(self) -> None:
        require_not_negative(self.current_bid, field_name="current_bid")
        require_not_negative(self.total_cost, field_name="total_cost")
        require_not_negative(self.bid_count, field_name="bid_count")


@dataclass(frozen=True)
class ClosingPrice:
    """The last bid seen on a lot while it was still open.

    What a lot actually sold for is not knowable here. The provider never
    publishes a hammer price, and a closed lot drops off the pages this reads,
    so the final bid is always one look too late. What is knowable is a floor:
    the lot sold for *at least* this much.

    ``seen_minutes_before_close`` says how tight that floor is, and is the
    whole value of the record. A bid read three minutes before the close is
    nearly the sale price; the same bid read six hours before says almost
    nothing. Every reader has to be able to tell those apart, so the two facts
    travel together and neither is stored without the other.
    """

    source: str
    listing_id: str
    title: str
    url: str
    last_bid: Decimal
    ends_at: datetime
    last_seen_at: datetime
    bid_count: int = 0
    estimated_retail: Decimal | None = None

    def __post_init__(self) -> None:
        require_not_negative(self.last_bid, field_name="last_bid")
        require_not_negative(self.bid_count, field_name="bid_count")
        if self.last_seen_at > self.ends_at:
            raise ValueError("last_seen_at must not be later than ends_at")

    @property
    def key(self) -> str:
        """The key a person copies to say which lot they mean."""
        return key_of(self.source, self.listing_id)

    @property
    def seen_minutes_before_close(self) -> int:
        """How long before the close this bid was true, rounded down."""
        return int((self.ends_at - self.last_seen_at).total_seconds() // 60)

    @property
    def share_of_retail(self) -> Decimal | None:
        """The floor price against the provider's estimate, when there is one."""
        if not self.estimated_retail:
            return None
        return self.last_bid / self.estimated_retail


@dataclass(frozen=True)
class WatchedItem:
    """One lot a person is following, and every look they have taken at it.

    The first block is what the provider said, refreshed on every run. The
    second is automatic match provenance. The third is what the person thinks,
    which no run may overwrite.
    """

    source: str
    listing_id: str
    inventory_id: str = ""
    title: str = ""
    url: str = ""
    photo_urls: tuple[str, ...] = ()
    estimated_retail: Decimal | None = None
    conditions: tuple[ConditionTag, ...] = ()
    quality_rating: int | None = None
    # What the scorer said when this lot entered the watchlist. These are
    # automatic provenance and may be refreshed as current rules are renamed.
    matched_interests: tuple[InterestRef, ...] = ()

    my_estimate: Decimal | None = None
    verdict: Verdict = Verdict.WATCHING
    note: str = ""
    # Which wants the person says this purchase actually satisfied. Winning a
    # multi-match lot never fills every want by accident.
    fulfilled_interests: tuple[InterestRef, ...] = ()
    # Whether a person has answered the fulfillment question, including the
    # valid answer "this purchase fulfilled none of them".
    fulfillment_reviewed: bool = False

    readings: tuple[PriceReading, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "verdict", _verdict(self.verdict))
        object.__setattr__(
            self,
            "matched_interests",
            _unique_interest_refs(self.matched_interests, field_name="matched_interests"),
        )
        object.__setattr__(
            self,
            "fulfilled_interests",
            _unique_interest_refs(
                self.fulfilled_interests, field_name="fulfilled_interests"
            ),
        )
        if not isinstance(self.fulfillment_reviewed, bool):
            raise ValueError("fulfillment_reviewed must be true or false")
        # Version-2 files written before this flag existed already used a
        # nonempty allocation as proof that a person answered the question.
        if self.fulfilled_interests and not self.fulfillment_reviewed:
            object.__setattr__(self, "fulfillment_reviewed", True)
        matched_ids = {
            reference.interest_id.casefold()
            for reference in self.matched_interests
        }
        unrecognized = [
            reference.name
            for reference in self.fulfilled_interests
            if reference.interest_id.casefold() not in matched_ids
        ]
        if unrecognized:
            raise ValueError(
                "fulfilled interests must be recorded matches: " + ", ".join(unrecognized)
            )
        if self.estimated_retail is not None:
            require_not_negative(self.estimated_retail, field_name="estimated_retail")
        if self.my_estimate is not None:
            require_not_negative(self.my_estimate, field_name="my_estimate")

    @property
    def key(self) -> str:
        """The auction this lot is in today, which is what a person types.

        Deliberately today's auction and not ``item_key``: a command acts on
        something that is open now, and this is the spelling the report the
        person is looking at also printed.
        """
        return key_of(self.source, self.listing_id)

    @property
    def item_key(self) -> str:
        """The thing itself, which is how one trail spans several auctions.

        This is the watchlist's identity for a row. It is not what a person
        types -- ``key`` is -- but both spell out the same way, so pasting the
        wrong one still finds the right row.
        """
        return key_of(self.source, self.inventory_id or self.listing_id)

    @property
    def auctions_seen(self) -> int:
        """How many separate auctions this item's trail covers."""
        return len({reading.listing_id for reading in self.readings if reading.listing_id})

    def answers_to(self, source: str, identifier: str) -> bool:
        """Match either name a person might have to hand.

        Someone reading the file has the item id; someone reading a URL has the
        auction id. Both should find the same entry.
        """
        return source == self.source and identifier in {
            self.inventory_id,
            self.listing_id,
            *(reading.listing_id for reading in self.readings),
        }

    @property
    def concerns(self) -> tuple[ConditionTag, ...]:
        """The condition tags that are not green, worst news first."""
        return tuple(tag for tag in self.conditions if tag.is_concerning)

    @property
    def condition_photo_url(self) -> str:
        """The photo of this actual lot, rather than the manufacturer's."""
        return self.photo_urls[-1] if self.photo_urls else ""

    @property
    def first(self) -> PriceReading | None:
        return self.readings[0] if self.readings else None

    @property
    def latest(self) -> PriceReading | None:
        return self.readings[-1] if self.readings else None

    @property
    def movement(self) -> Decimal | None:
        """How far the bid has travelled since the first look, if there were two."""
        if len(self.readings) < 2:
            return None
        return self.readings[-1].current_bid - self.readings[0].current_bid

    @property
    def headroom(self) -> Decimal | None:
        """What is left between the latest total and the person's own estimate.

        Negative means it has already cost more than they said it was worth,
        which is the number worth seeing before bidding again.
        """
        if self.my_estimate is None or self.latest is None:
            return None
        return self.my_estimate - self.latest.total_cost


def _verdict(verdict: Any) -> Verdict:
    """Accept either the word or the member, and return the member."""
    for allowed in Verdict:
        if verdict == allowed:
            return allowed
    choices = ", ".join(Verdict)
    raise ValueError(f"verdict must be one of: {choices}")
