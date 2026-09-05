"""The domain model every other module speaks.

These records are immutable and free of behavior on purpose: acquisition,
scoring, valuation, storage, and reporting all pass them around, so the model
stays the one thing in the project with no dependencies of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .fields import (
    is_absent,
    parse_dimensions,
    parse_labels,
    parse_money,
    parse_optional_decimal,
    parse_optional_money,
    parse_optional_rate,
    parse_utc_datetime,
    parse_whole_number,
)

REQUIRED_LISTING_FIELDS = ("source", "listing_id", "title", "url", "current_bid")


@dataclass(frozen=True)
class Listing:
    """One auction lot as the provider described it at one moment in time."""

    source: str
    listing_id: str
    title: str
    url: str
    current_bid: Decimal
    estimated_retail: Decimal | None = None
    bid_count: int = 0
    ends_at: datetime | None = None
    location: str = ""
    conditions: tuple[str, ...] = ()
    image_url: str = ""
    buyer_premium_rate: Decimal | None = None
    brand: str = ""
    model: str = ""
    category: str = ""
    handling_weight_lb: Decimal | None = None
    package_dimensions_in: tuple[Decimal, ...] = ()
    loading_assistance: tuple[str, ...] = ()
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Listing":
        """Build a listing from one canonical JSON object or CSV row."""
        missing = [key for key in REQUIRED_LISTING_FIELDS if is_absent(data.get(key))]
        if missing:
            raise ValueError(f"missing required listing fields: {', '.join(missing)}")
        return cls(
            source=_text(data, "source"),
            listing_id=_text(data, "listing_id"),
            title=_text(data, "title"),
            url=_text(data, "url"),
            current_bid=parse_money(data["current_bid"], field_name="current_bid"),
            estimated_retail=parse_optional_money(
                data.get("estimated_retail"), field_name="estimated_retail"
            ),
            bid_count=parse_whole_number(data.get("bid_count"), field_name="bid_count"),
            ends_at=parse_utc_datetime(data.get("ends_at"), field_name="ends_at"),
            location=_text(data, "location"),
            conditions=parse_labels(data.get("conditions")),
            image_url=_text(data, "image_url"),
            buyer_premium_rate=parse_optional_rate(
                data.get("buyer_premium_rate"), field_name="buyer_premium_rate"
            ),
            brand=_text(data, "brand"),
            model=_text(data, "model"),
            category=_text(data, "category").lower(),
            handling_weight_lb=parse_optional_decimal(
                data.get("handling_weight_lb"), field_name="handling_weight_lb"
            ),
            package_dimensions_in=parse_dimensions(data.get("package_dimensions_in")),
            loading_assistance=parse_labels(data.get("loading_assistance")),
            observed_at=parse_utc_datetime(data.get("observed_at"), field_name="observed_at")
            or datetime.now(timezone.utc),
        )


@dataclass(frozen=True)
class ObservationChange:
    """What the stored history says about a listing seen again."""

    is_new: bool
    price_changed: bool
    previous_bid: Decimal | None = None


@dataclass(frozen=True)
class LogisticsDecision:
    """An operator's saved answer to a handling question for one listing."""

    status: str
    added_cost: Decimal = Decimal("0")
    note: str = ""


@dataclass(frozen=True)
class LogisticsAssessment:
    """What handling stages are still unresolved for one listing."""

    status: str
    questions: tuple[str, ...] = ()
    added_cost: Decimal = Decimal("0")
    decision_note: str = ""


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


@dataclass(frozen=True)
class Candidate:
    """One listing that matched one rule, with the evidence for reporting it."""

    listing: Listing
    category: str
    rule_name: str
    score: int
    total_cost: Decimal
    retail_ratio: Decimal | None
    reasons: tuple[str, ...]
    change: ObservationChange
    valuation: ValuationSummary | None = None
    logistics: LogisticsAssessment | None = None


def _text(data: dict[str, Any], key: str) -> str:
    return str(data.get(key) or "").strip()
