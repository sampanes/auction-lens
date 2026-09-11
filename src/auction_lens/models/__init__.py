"""The domain model every other module speaks.

These records are immutable and free of behavior on purpose: acquisition,
scoring, valuation, storage, and reporting all pass them around, so the model
stays the one thing in the project with no dependencies of its own.

A record that is built from outside input checks itself in ``__post_init__``,
so an invalid one cannot exist for any caller to trip over. Records that are
*derived* from already-checked ones do not re-check; see docs/CONVENTIONS.md.

One subject per module, and this file is the door. Nothing outside needs to
know which module a record lives in, and nothing inside may import from here.

    scale       the 0-100 every score lives on, and what a want can reach of it
    lots        one auction lot as the provider described it, and its name
    handling    whether it can be got home, and what the operator decided
    valuation   what price sources said, with provenance attached
    interests   which want a lot answered, and how that want is getting on
    candidates  a lot plus the reason it is reported; ordering and capping
    watching    what a person thinks of a lot, and every price they have seen
"""

from __future__ import annotations

from .candidates import (
    Candidate,
    CandidateCategory,
    InterestHarvest,
    ReadingOrder,
    best_of_each,
    harvest_of,
    ranked,
)
from .handling import (
    OPERATOR_DECIDABLE,
    LogisticsAssessment,
    LogisticsDecision,
    LogisticsStatus,
)
from .interests import InterestProgress, InterestRef
from .lots import (
    KEY_SEPARATOR,
    REQUIRED_LISTING_FIELDS,
    Listing,
    ObservationChange,
    key_of,
)
from .scale import (
    BASE_INTEREST_SCORE,
    ENDING_SOON_BONUS,
    HIGHEST_INTEREST_SCORE,
    HIGHEST_SCORE,
    LOWEST_SCORE,
    NEW_LISTING_PRIORITY_BONUS,
    PRICE_CHANGE_PRIORITY_BONUS,
)
from .valuation import (
    ResearchLink,
    ValuationBand,
    ValuationObservation,
    ValuationSummary,
)
from .watching import ClosingPrice, PriceReading, Verdict, WatchedItem

__all__ = [
    "BASE_INTEREST_SCORE",
    "ENDING_SOON_BONUS",
    "HIGHEST_INTEREST_SCORE",
    "HIGHEST_SCORE",
    "KEY_SEPARATOR",
    "LOWEST_SCORE",
    "NEW_LISTING_PRIORITY_BONUS",
    "OPERATOR_DECIDABLE",
    "PRICE_CHANGE_PRIORITY_BONUS",
    "REQUIRED_LISTING_FIELDS",
    "Candidate",
    "CandidateCategory",
    "ClosingPrice",
    "InterestHarvest",
    "InterestProgress",
    "InterestRef",
    "Listing",
    "LogisticsAssessment",
    "LogisticsDecision",
    "LogisticsStatus",
    "ObservationChange",
    "PriceReading",
    "ReadingOrder",
    "ResearchLink",
    "ValuationBand",
    "ValuationObservation",
    "ValuationSummary",
    "Verdict",
    "WatchedItem",
    "best_of_each",
    "harvest_of",
    "key_of",
    "ranked",
]
