"""Auction costs and the configured sources used to estimate value."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..values import require_finite, require_not_negative, require_rate

# Source ids name cache files and report rows, so they stay filesystem-safe.
SOURCE_ID_CHARACTERS = "letters, digits, and _ . -"


@dataclass(frozen=True)
class EconomicsConfig:
    """The fees that turn a winning bid into the amount actually paid."""

    default_buyer_premium: Decimal = Decimal("0")
    premium_is_taxable: bool = True
    sales_tax_rate: Decimal = Decimal("0")
    processing_fee: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        require_rate(self.default_buyer_premium, field_name="default_buyer_premium")
        require_rate(self.sales_tax_rate, field_name="sales_tax_rate")
        require_not_negative(self.processing_fee, field_name="processing_fee")


@dataclass(frozen=True)
class ValuationSourceConfig:
    """One configured source instance consumed by a reusable adapter."""

    source_id: str
    adapter: str
    enabled: bool = True
    label: str = ""
    categories: tuple[str, ...] = ()
    weight: Decimal = Decimal("1")
    settings: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_safe_identifier(self.source_id):
            raise ValueError(
                f"id {self.source_id!r} may contain only {SOURCE_ID_CHARACTERS}, "
                "and must start with a letter or digit"
            )
        require_finite(self.weight, field_name="weight")
        if self.weight <= 0:
            raise ValueError("weight must be greater than zero")


@dataclass(frozen=True)
class ValuationConfig:
    """Every price source, and the one currency their answers are read in."""

    enabled: bool = False
    currency: str = "USD"
    sources: tuple[ValuationSourceConfig, ...] = ()

    def __post_init__(self) -> None:
        identifiers = [source.source_id for source in self.sources]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("source ids must be unique")


def _is_safe_identifier(value: str) -> bool:
    """Accept a name that is safe in a file path and readable in a report."""
    if not value or not value[0].isalnum() or not value.isascii():
        return False
    return all(character.isalnum() or character in "_.-" for character in value)
