"""What price sources said a thing is worth, with the provenance kept attached.

Every number here came from somewhere, and aggregation is only auditable if the
record remembers where. Nothing in this module fetches anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..fields import require_at_least, require_finite, require_not_negative


@dataclass(frozen=True)
class ValuationObservation:
    """A source's price claim; retaining provenance keeps aggregation auditable."""

    source_id: str
    basis: str
    low: Decimal
    typical: Decimal
    high: Decimal
    currency: str = "USD"
    sample_size: int = 1
    confidence: Decimal = Decimal("1")
    observed_at: datetime | None = None
    url: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        for field_name in ("low", "typical", "high"):
            require_finite(getattr(self, field_name), field_name=field_name)
        if not self.low <= self.typical <= self.high:
            raise ValueError("valuation requires low <= typical <= high")
        require_at_least(self.sample_size, 1, field_name="sample_size")
        require_not_negative(self.confidence, field_name="confidence")


@dataclass(frozen=True)
class ValuationBand:
    """Several observations of one basis, combined into a single range."""

    basis: str
    low: Decimal
    typical: Decimal
    high: Decimal
    source_count: int
    sample_size: int


@dataclass(frozen=True)
class ResearchLink:
    """A place for a person to check value; nothing is fetched from it."""

    source_id: str
    label: str
    url: str


@dataclass(frozen=True)
class ValuationSummary:
    """Everything the valuation fan-out learned about one listing."""

    bands: tuple[ValuationBand, ...] = ()
    observations: tuple[ValuationObservation, ...] = ()
    research_links: tuple[ResearchLink, ...] = ()
    errors: tuple[str, ...] = ()
