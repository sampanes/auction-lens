"""Combining independent price observations into one band per basis.

The median is weighted rather than averaged so that one confident source with
a large sample cannot be cancelled out by an outlier, and capped so that one
enormous dataset cannot silence every independent source either.
"""

from __future__ import annotations

from decimal import Decimal

from ..models import ValuationBand, ValuationObservation

# A source that reports no confidence still counts, but only barely.
MINIMUM_CONFIDENCE = Decimal("0.01")

# Beyond this many comparables, more rows stop buying more influence.
SAMPLE_SIZE_CAP = 25

BAND_EDGES = ("low", "typical", "high")


def combine_into_bands(
    observations: list[ValuationObservation],
) -> tuple[ValuationBand, ...]:
    """Group like-for-like evidence and reduce each group to a single range."""
    bands = []
    for basis in sorted({item.basis for item in observations}):
        group = [item for item in observations if item.basis == basis]
        low, typical, high = (weighted_median(group, edge) for edge in BAND_EDGES)
        bands.append(
            ValuationBand(
                basis=basis,
                low=low,
                typical=typical,
                high=high,
                source_count=len({item.source_id for item in group}),
                sample_size=sum(item.sample_size for item in group),
            )
        )
    return tuple(bands)


def weighted_median(observations: list[ValuationObservation], edge: str) -> Decimal:
    """The value at which half of the group's total weight has been reached."""
    weighted = sorted((getattr(item, edge), _weight_of(item)) for item in observations)
    halfway = sum(weight for _, weight in weighted) / 2
    reached = Decimal("0")
    for value, weight in weighted:
        reached += weight
        if reached >= halfway:
            return value
    return weighted[-1][0]


def _weight_of(observation: ValuationObservation) -> Decimal:
    confidence = max(MINIMUM_CONFIDENCE, observation.confidence)
    return confidence * Decimal(min(observation.sample_size, SAMPLE_SIZE_CAP))
