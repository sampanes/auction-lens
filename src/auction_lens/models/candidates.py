"""One lot that matched one rule, and how a pile of them is ordered and capped.

A candidate is the unit of a report: not a lot, but a lot together with the
reason it is being shown. The same lot can be two candidates, once because it
matches a want and once because it is cheap against its stated retail.

Ordering lives here too, because ordering is a property of the pile rather than
of any renderer, and two renderers disagreeing about it would be a bug nobody
would see.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .handling import LogisticsAssessment
from .lots import Listing, ObservationChange
from .scale import HIGHEST_SCORE
from .valuation import ValuationSummary


class CandidateCategory(StrEnum):
    """Why a listing is being reported at all."""

    WANTED = "wanted"
    ANOMALY = "anomaly"


@dataclass(frozen=True)
class Candidate:
    """One listing that matched one rule, with the evidence for reporting it."""

    listing: Listing
    category: CandidateCategory
    rule_id: str
    rule_name: str
    score: int
    total_cost: Decimal
    retail_ratio: Decimal | None
    reasons: tuple[str, ...]
    change: ObservationChange
    valuation: ValuationSummary | None = None
    logistics: LogisticsAssessment | None = None
    weight: Decimal = Decimal("1")

    @property
    def section(self) -> str:
        """What this lot is one *of*, which is how a reader groups them.

        The interest it matched, or, for a lot reported on price alone, the
        reason it was reported. One name, so that capping, tallying, and
        grouping cannot disagree about what counts as the same kind of thing.
        """
        return self.rule_name or str(self.category)

    @property
    def priority(self) -> Decimal:
        """Reading order: quality plus fresh news, scaled by how much it was wanted.

        Deliberately separate from ``score``. Score answers "is this worth
        reporting at all", and every configured bar is tuned against it.
        Priority answers "what should be read first". A new listing or changed
        price can move an already-qualified candidate up, but cannot push it
        past a bar. The clamp preserves the same ceiling as every score.
        """
        attention_score = min(HIGHEST_SCORE, self.score + self.change.priority_bonus)
        return attention_score * self.weight


@dataclass(frozen=True)
class InterestHarvest:
    """How many lots one kind of thing found today, and how many are shown."""

    name: str
    found: int
    shown: int

    @property
    def withheld(self) -> int:
        """The ones the report is deliberately not printing."""
        return self.found - self.shown

    @property
    def is_crowded(self) -> bool:
        """Whether this kind found more than the report is willing to print."""
        return self.withheld > 0


class ReadingOrder(StrEnum):
    """What "first" means in a report.

    Priority is the default and the one every bar is tuned against: how good a
    lot is, scaled by how much this operator wanted it.

    Retail answers a different question -- what is the most valuable thing here
    -- which is the one somebody asks when they are about to go and collect,
    and it deliberately ignores how well the lot scored.
    """

    PRIORITY = "priority"
    RETAIL = "retail"


def ranked(
    candidates: list[Candidate],
    limit: int | None = None,
    order: ReadingOrder = ReadingOrder.PRIORITY,
) -> list[Candidate]:
    """Best first, and optionally only the best few.

    One authority for reading order, because a cap means "the best" only if
    whatever applies it agrees with whatever renders it about which those are.

    The limit takes the top of the existing ranking rather than introducing a
    bar of its own: the weights decide what is worth reading, and this only
    decides how long a report a person will actually finish.
    """
    best = sorted(candidates, key=_reading_key(order), reverse=True)
    return best if limit is None else best[:limit]


def best_of_each(candidates: list[Candidate], most_each: int) -> list[Candidate]:
    """The best few of each kind, so one crowded want cannot spend the report.

    Ten near-identical keyboards are ten answers to the same question. Keeping
    the best few of each leaves room for everything else that matched today,
    and what is held back is neither lost nor hidden: the caller still has
    every candidate, and the report says how many it is not showing and how to
    reach them at the provider's end.

    Deliberately by the same ranking as everything else, so "the best few"
    means what it means everywhere.
    """
    kept: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in ranked(candidates):
        section = kept[candidate.section]
        if len(section) < most_each:
            section.append(candidate)
    return ranked([candidate for section in kept.values() for candidate in section])


def harvest_of(
    found: list[Candidate], shown: list[Candidate]
) -> tuple[InterestHarvest, ...]:
    """How much of each kind matched today, beside how much of it is on the page.

    Both numbers together, because either alone misleads: "three telescopes"
    reads as the whole crop, and "eleven" reads as eleven links.
    """
    total = Counter(candidate.section for candidate in found)
    printed = Counter(candidate.section for candidate in shown)
    return tuple(
        InterestHarvest(name=name, found=count, shown=printed.get(name, 0))
        for name, count in total.most_common()
    )


def _reading_key(order: ReadingOrder):
    """The one value each ordering sorts on.

    A lot with no stated retail sorts last rather than first, because an
    unknown value is not a large one. Priority breaks ties either way, so two
    lots of the same worth still arrive in a sensible order.
    """
    if order == ReadingOrder.RETAIL:
        return lambda item: (item.listing.estimated_retail or Decimal(0), item.priority)
    return lambda item: (item.priority,)
