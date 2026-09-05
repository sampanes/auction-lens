from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


def money(value: Any, *, field_name: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if amount < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return amount.quantize(Decimal("0.01"))


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_conditions(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    values = value if isinstance(value, list) else str(value).split("|")
    return tuple(sorted({str(item).strip().lower() for item in values if str(item).strip()}))


def optional_decimal(value: Any, *, field_name: str) -> Decimal | None:
    return None if value in (None, "") else money(value, field_name=field_name)


def parse_dimensions(value: Any) -> tuple[Decimal, ...]:
    if value in (None, ""):
        return ()
    values = value if isinstance(value, list) else str(value).lower().replace("×", "x").split("x")
    dimensions = tuple(money(item, field_name="package_dimensions_in") for item in values)
    if len(dimensions) not in {2, 3}:
        raise ValueError("package_dimensions_in must contain two or three dimensions")
    return dimensions


@dataclass(frozen=True)
class Listing:
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
        required = ("source", "listing_id", "title", "url", "current_bid")
        missing = [key for key in required if data.get(key) in (None, "")]
        if missing:
            raise ValueError(f"missing required listing fields: {', '.join(missing)}")
        retail = data.get("estimated_retail")
        premium = data.get("buyer_premium_rate")
        return cls(
            source=str(data["source"]).strip(),
            listing_id=str(data["listing_id"]).strip(),
            title=str(data["title"]).strip(),
            url=str(data["url"]).strip(),
            current_bid=money(data["current_bid"], field_name="current_bid"),
            estimated_retail=None if retail in (None, "") else money(retail, field_name="estimated_retail"),
            bid_count=int(data.get("bid_count") or 0),
            ends_at=parse_datetime(data.get("ends_at")),
            location=str(data.get("location") or "").strip(),
            conditions=parse_conditions(data.get("conditions")),
            image_url=str(data.get("image_url") or "").strip(),
            buyer_premium_rate=None if premium in (None, "") else Decimal(str(premium)),
            brand=str(data.get("brand") or "").strip(),
            model=str(data.get("model") or "").strip(),
            category=str(data.get("category") or "").strip().lower(),
            handling_weight_lb=optional_decimal(
                data.get("handling_weight_lb"), field_name="handling_weight_lb"
            ),
            package_dimensions_in=parse_dimensions(data.get("package_dimensions_in")),
            loading_assistance=parse_conditions(data.get("loading_assistance")),
            observed_at=parse_datetime(data.get("observed_at")) or datetime.now(timezone.utc),
        )


@dataclass(frozen=True)
class ObservationChange:
    is_new: bool
    price_changed: bool
    previous_bid: Decimal | None = None


@dataclass(frozen=True)
class LogisticsDecision:
    status: str
    added_cost: Decimal = Decimal("0")
    note: str = ""


@dataclass(frozen=True)
class LogisticsAssessment:
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
    basis: str
    low: Decimal
    typical: Decimal
    high: Decimal
    source_count: int
    sample_size: int


@dataclass(frozen=True)
class ResearchLink:
    source_id: str
    label: str
    url: str


@dataclass(frozen=True)
class ValuationSummary:
    bands: tuple[ValuationBand, ...] = ()
    observations: tuple[ValuationObservation, ...] = ()
    research_links: tuple[ResearchLink, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
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
