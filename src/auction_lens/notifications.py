"""Decide what a delivery should contain, without sending or remembering it.

Observation history answers what the collector saw. This module instead
compares a proposed report with the last report one destination accepted. It
is deliberately pure: callers may safely plan several channels, then record
only the receipts for channels that actually succeeded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TypeVar

from .models import (
    Candidate,
    InterestProgress,
    ObservationChange,
    WatchedItem,
    ranked,
)

UNKNOWN_REVISION = "unknown"
SHA256_HEX_LENGTH = 64
LOWERCASE_HEX = frozenset("0123456789abcdef")


class ReportKind(StrEnum):
    """The independently delivered reports Auction Lens can produce."""

    FINDINGS = "findings"
    WATCHLIST = "watchlist"


class DeliveryChannel(StrEnum):
    """A destination type whose success is recorded independently."""

    EMAIL = "email"
    WEBHOOK = "webhook"


@dataclass(frozen=True)
class DeliveryRoute:
    """One report at one opaque destination, with no address stored here."""

    report_kind: ReportKind
    channel: DeliveryChannel
    destination_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "report_kind",
            _enum_member(ReportKind, self.report_kind, field_name="report_kind"),
        )
        object.__setattr__(
            self,
            "channel",
            _enum_member(DeliveryChannel, self.channel, field_name="channel"),
        )
        fingerprint = self.destination_fingerprint
        if not isinstance(fingerprint, str):
            raise ValueError("destination_fingerprint must be non-empty text")
        if len(fingerprint) != SHA256_HEX_LENGTH or not set(fingerprint) <= LOWERCASE_HEX:
            raise ValueError(
                "destination_fingerprint must be a full lowercase SHA-256 fingerprint"
            )
        object.__setattr__(self, "destination_fingerprint", fingerprint)

    @property
    def key(self) -> tuple[str, str, str]:
        """The non-personal identity suitable for a private delivery ledger."""
        return (
            str(self.report_kind),
            str(self.channel),
            self.destination_fingerprint,
        )


@dataclass(frozen=True)
class DeliveryItem:
    """The revision of one auction event represented in a successful report."""

    source: str
    listing_id: str
    revision: str

    def __post_init__(self) -> None:
        for field_name in ("source", "listing_id", "revision"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name=field_name),
            )

    @property
    def key(self) -> tuple[str, str]:
        """An auction ID, intentionally not the cross-relisting inventory ID."""
        return self.source, self.listing_id


@dataclass(frozen=True)
class CandidateDeliveryPlan:
    """Reportable matches and the receipts to save if delivery succeeds."""

    candidates: tuple[Candidate, ...]
    receipts: tuple[DeliveryItem, ...]
    unchanged_matches: int = 0
    held_back_matches: int = 0


@dataclass(frozen=True)
class WatchlistDeliveryPlan:
    """Changed followed lots and the receipts to save if delivery succeeds."""

    items: tuple[WatchedItem, ...]
    receipts: tuple[DeliveryItem, ...]
    unchanged_items: int = 0


def candidate_items(candidates: Iterable[Candidate]) -> tuple[DeliveryItem, ...]:
    """Project candidate matches onto unique auction-event revisions."""
    return _unique_receipts(_candidate_item(candidate) for candidate in candidates)


def watchlist_items(items: Iterable[WatchedItem]) -> tuple[DeliveryItem, ...]:
    """Project followed lots onto unique auction-event revisions."""
    return _unique_receipts(_watchlist_item(item) for item in items)


def plan_candidates(
    candidates: Iterable[Candidate],
    delivered: Mapping[tuple[str, str], str],
    limit: int | None = None,
    repeat: bool = False,
) -> CandidateDeliveryPlan:
    """Filter against successful delivery state, then rank and cap.

    Filtering first matters: an unchanged high-scoring lot from the morning
    must not occupy the evening cap and hide a lower-scoring new lot. Candidate
    ``change`` is replaced with delivery-relative history so the message says
    what this recipient last received, not merely what the collector last saw.
    """
    if limit is not None and (isinstance(limit, bool) or limit < 1):
        raise ValueError("limit must be at least 1")

    eligible: list[Candidate] = []
    unchanged = 0
    for candidate in candidates:
        item = _candidate_item(candidate)
        previous = delivered.get(item.key)
        unseen = item.key not in delivered
        changed = not unseen and not _same_price(previous, candidate.listing.current_bid)
        if not (unseen or changed or repeat):
            unchanged += 1
            continue
        eligible.append(
            replace(
                candidate,
                change=ObservationChange(
                    is_new=unseen,
                    price_changed=changed,
                    previous_bid=None if unseen else _decimal(previous),
                ),
            )
        )

    # The configured presentation order must not decide what survives a cap.
    # Quality priority selects the page; each renderer may rearrange that page.
    selected = tuple(ranked(eligible, limit=limit))
    return CandidateDeliveryPlan(
        candidates=selected,
        receipts=candidate_items(selected),
        unchanged_matches=unchanged,
        held_back_matches=len(eligible) - len(selected),
    )


def plan_watchlist(
    items: Iterable[WatchedItem],
    delivered: Mapping[tuple[str, str], str],
    repeat: bool = False,
) -> WatchlistDeliveryPlan:
    """Keep followed lots that are new, changed, or explicitly repeated."""
    selected = []
    unchanged = 0
    for watched in items:
        receipt = _watchlist_item(watched)
        unseen = receipt.key not in delivered
        changed = not unseen and not _same_revision(delivered[receipt.key], receipt.revision)
        if not (unseen or changed or repeat):
            unchanged += 1
            continue
        selected.append(watched)
    return WatchlistDeliveryPlan(
        items=tuple(selected),
        receipts=watchlist_items(selected),
        unchanged_items=unchanged,
    )


def outcome_fingerprint(
    progress: Iterable[InterestProgress], unreviewed_wins: int
) -> str:
    """Hash finite outcome facts, independent of report wording and order."""
    if (
        isinstance(unreviewed_wins, bool)
        or not isinstance(unreviewed_wins, int)
        or unreviewed_wins < 0
    ):
        raise ValueError("unreviewed_wins must be a non-negative integer")
    finite = sorted(
        (
            {
                "id": item.interest.interest_id,
                "name": item.interest.name,
                "wanted": item.wanted,
                "fulfilled": item.fulfilled,
            }
            for item in progress
            if item.wanted is not None
        ),
        key=lambda item: (str(item["id"]).casefold(), str(item["id"])),
    )
    canonical = json.dumps(
        {"finite": finite, "unreviewed_wins": unreviewed_wins},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _candidate_item(candidate: Candidate) -> DeliveryItem:
    listing = candidate.listing
    return DeliveryItem(
        source=listing.source,
        listing_id=listing.listing_id,
        revision=_price_revision(listing.current_bid),
    )


def _watchlist_item(item: WatchedItem) -> DeliveryItem:
    revision = (
        UNKNOWN_REVISION
        if item.latest is None
        else _price_revision(item.latest.current_bid)
    )
    return DeliveryItem(item.source, item.listing_id, revision)


def _price_revision(amount: Decimal) -> str:
    """Give equal decimal prices one stable spelling."""
    if amount == 0:
        return "0"
    rendered = format(amount, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _same_price(previous: str | None, current: Decimal) -> bool:
    parsed = _decimal(previous)
    return parsed is not None and parsed == current


def _same_revision(previous: str, current: str) -> bool:
    """Compare numeric revisions by value while preserving ``unknown``."""
    previous_price = _decimal(previous)
    current_price = _decimal(current)
    if previous_price is not None and current_price is not None:
        return previous_price == current_price
    return previous == current


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _unique_receipts(items: Iterable[DeliveryItem]) -> tuple[DeliveryItem, ...]:
    """Record an auction event once even when it matched several rules."""
    unique = {}
    for item in items:
        unique.setdefault(item.key, item)
    return tuple(unique.values())


EnumType = TypeVar("EnumType", bound=StrEnum)


def _enum_member(
    enum_type: type[EnumType], value: object, *, field_name: str
) -> EnumType:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        choices = ", ".join(enum_type)
        raise ValueError(f"{field_name} must be one of: {choices}") from error


def _required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()
