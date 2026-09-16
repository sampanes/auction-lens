"""Strict records for private feedback and reviewable configuration proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from ..values import require_not_negative, require_within

MAX_IDENTIFIER_LENGTH = 200
MAX_NAME_LENGTH = 200
MAX_NOTE_LENGTH = 500
MAX_TITLE_LENGTH = 500
SHA256_LENGTH = 64
NO_CONFIG_CHANGE_NOTICE = "No configuration was changed."


class FeedbackLabel(StrEnum):
    """The small, durable vocabulary an operator can teach the tool with."""

    YES = "yes"
    MAYBE = "maybe"
    NO = "no"
    WRONG_ITEM = "wrong-item"
    TOO_EXPENSIVE = "too-expensive"
    LOGISTICS_IMPOSSIBLE = "logistics-impossible"


class FeedbackAction(StrEnum):
    """Whether an event sets feedback or deliberately removes it."""

    RECORD = "record"
    CLEAR = "clear"


class FeedbackTargetKind(StrEnum):
    """The rule family a decision is evidence about."""

    INTEREST = "interest"
    ANOMALY = "anomaly"


@dataclass(frozen=True)
class FeedbackTarget:
    """A stable rule identity plus the human name shown when it was recorded."""

    kind: FeedbackTargetKind
    target_id: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", FeedbackTargetKind(self.kind))
        object.__setattr__(
            self, "target_id", _bounded(self.target_id, "target id", MAX_IDENTIFIER_LENGTH)
        )
        object.__setattr__(self, "name", _bounded(self.name, "target name", MAX_NAME_LENGTH))

    @property
    def key(self) -> tuple[str, str]:
        """Case-insensitive identity used across display-name changes."""
        return (self.kind.value, self.target_id.casefold())


@dataclass(frozen=True)
class FeedbackEvidence:
    """Facts visible at the decision, without links or private profile text."""

    item_key: str
    source: str
    listing_id: str
    inventory_id: str = ""
    title: str = ""
    observed_at: datetime | None = None
    all_in_cost: Decimal | None = None
    estimated_retail: Decimal | None = None
    condition_labels: tuple[str, ...] = ()
    quality_rating: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "item_key", _bounded(self.item_key, "item key", MAX_IDENTIFIER_LENGTH)
        )
        object.__setattr__(self, "source", _bounded(self.source, "source", MAX_NAME_LENGTH))
        object.__setattr__(
            self,
            "listing_id",
            _bounded(self.listing_id, "listing id", MAX_IDENTIFIER_LENGTH),
        )
        object.__setattr__(
            self,
            "inventory_id",
            _bounded_optional(self.inventory_id, "inventory id", MAX_IDENTIFIER_LENGTH),
        )
        object.__setattr__(
            self, "title", _bounded_optional(self.title, "title", MAX_TITLE_LENGTH)
        )
        if self.observed_at is not None:
            object.__setattr__(
                self, "observed_at", _aware_utc(self.observed_at, "observed_at")
            )
        for field_name in ("all_in_cost", "estimated_retail"):
            value = getattr(self, field_name)
            if value is not None:
                _require_decimal(value, field_name)
                require_not_negative(value, field_name=field_name)
        labels = tuple(
            _bounded(label, "condition label", MAX_NAME_LENGTH)
            for label in self.condition_labels
        )
        object.__setattr__(self, "condition_labels", labels)
        if self.quality_rating is not None and (
            isinstance(self.quality_rating, bool) or not isinstance(self.quality_rating, int)
        ):
            raise ValueError("quality_rating must be a whole number")
        if self.quality_rating is not None:
            require_within(
                self.quality_rating,
                low=1,
                high=5,
                field_name="quality_rating",
            )

    @property
    def retail_ratio(self) -> Decimal | None:
        """Exact decimal arithmetic; never a binary floating-point estimate."""
        if (
            self.all_in_cost is None
            or self.estimated_retail is None
            or self.estimated_retail == 0
        ):
            return None
        return self.all_in_cost / self.estimated_retail


@dataclass(frozen=True)
class FeedbackEvent:
    """One immutable entry in the append-only feedback history."""

    event_id: str
    recorded_at: datetime
    action: FeedbackAction
    item_key: str
    target: FeedbackTarget
    config_sha256: str
    evidence: FeedbackEvidence
    label: FeedbackLabel | None = None
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", FeedbackAction(self.action))
        object.__setattr__(self, "recorded_at", _aware_utc(self.recorded_at, "recorded_at"))
        object.__setattr__(self, "event_id", _event_id(self.event_id))
        object.__setattr__(
            self, "item_key", _bounded(self.item_key, "item key", MAX_IDENTIFIER_LENGTH)
        )
        object.__setattr__(self, "config_sha256", require_sha256(self.config_sha256))
        object.__setattr__(
            self, "note", _bounded_optional(self.note, "note", MAX_NOTE_LENGTH)
        )
        if not isinstance(self.target, FeedbackTarget):
            raise ValueError("event target must be a feedback target")
        if not isinstance(self.evidence, FeedbackEvidence):
            raise ValueError("event evidence must be feedback evidence")
        if self.item_key != self.evidence.item_key:
            raise ValueError("event item key must match its evidence item key")
        if self.action == FeedbackAction.RECORD:
            if self.label is None:
                raise ValueError("record events require a feedback label")
            object.__setattr__(self, "label", FeedbackLabel(self.label))
        elif self.label is not None or self.note:
            raise ValueError("clear events cannot contain a label or note")

    @property
    def current_key(self) -> tuple[str, str, str]:
        return (self.item_key, *self.target.key)

    def has_same_meaning_as(self, other: FeedbackEvent) -> bool:
        """Ignore clocks and snapshots when a repeated command changes no opinion."""
        return (
            self.current_key == other.current_key
            and self.action == other.action
            and self.label == other.label
            and self.note == other.note
        )


@dataclass(frozen=True)
class FeedbackPattern:
    """Repeated current feedback worth a person's review."""

    target: FeedbackTarget
    label: FeedbackLabel
    distinct_items: int
    evidence_ids: tuple[str, ...]
    summary: str
    notice: str = NO_CONFIG_CHANGE_NOTICE

    def __post_init__(self) -> None:
        if not isinstance(self.target, FeedbackTarget):
            raise ValueError("pattern target must be a feedback target")
        object.__setattr__(self, "label", FeedbackLabel(self.label))
        if isinstance(self.distinct_items, bool) or not isinstance(
            self.distinct_items, int
        ):
            raise ValueError("distinct_items must be a whole number")
        if self.distinct_items < 1:
            raise ValueError("distinct_items must be at least 1")
        evidence_ids = tuple(_event_id(value) for value in self.evidence_ids)
        if len(evidence_ids) != self.distinct_items:
            raise ValueError("distinct_items must equal the number of evidence ids")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(
            self, "summary", _bounded(self.summary, "summary", MAX_TITLE_LENGTH)
        )
        _require_notice(self.notice)


@dataclass(frozen=True)
class ProposalChange:
    """One typed, review-only change to one interest setting."""

    field: str
    before: Decimal | None
    after: Decimal

    def __post_init__(self) -> None:
        if self.field not in {"max_total_cost", "maximum_retail_ratio"}:
            raise ValueError(f"unsupported proposal field: {self.field}")
        _require_decimal(self.after, "proposal after")
        require_not_negative(self.after, field_name="proposal after")
        if self.before is not None:
            _require_decimal(self.before, "proposal before")
            require_not_negative(self.before, field_name="proposal before")
            if self.after >= self.before:
                raise ValueError("a feedback proposal must narrow the current setting")


@dataclass(frozen=True)
class FeedbackProposal:
    """A deterministic suggestion that never edits configuration itself."""

    proposal_id: str
    target: FeedbackTarget
    config_sha256: str
    changes: tuple[ProposalChange, ...]
    evidence_ids: tuple[str, ...]
    notice: str = NO_CONFIG_CHANGE_NOTICE

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _proposal_id(self.proposal_id))
        object.__setattr__(self, "config_sha256", require_sha256(self.config_sha256))
        if not isinstance(self.target, FeedbackTarget):
            raise ValueError("proposal target must be a feedback target")
        if self.target.kind != FeedbackTargetKind.INTEREST:
            raise ValueError("only an interest can receive a configuration proposal")
        if not self.changes:
            raise ValueError("a proposal requires at least one change")
        if not self.evidence_ids:
            raise ValueError("a proposal requires evidence ids")
        evidence_ids = tuple(_event_id(value) for value in self.evidence_ids)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("proposal evidence ids must be unique")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        fields = [change.field for change in self.changes]
        if len(fields) != len(set(fields)):
            raise ValueError("a proposal cannot change the same field twice")
        _require_notice(self.notice)


@dataclass(frozen=True)
class FeedbackReview:
    """Patterns and proposals derived without changing configuration."""

    patterns: tuple[FeedbackPattern, ...] = ()
    proposals: tuple[FeedbackProposal, ...] = ()
    notice: str = NO_CONFIG_CHANGE_NOTICE

    def __post_init__(self) -> None:
        if not all(isinstance(pattern, FeedbackPattern) for pattern in self.patterns):
            raise ValueError("patterns must contain feedback patterns")
        if not all(isinstance(proposal, FeedbackProposal) for proposal in self.proposals):
            raise ValueError("proposals must contain feedback proposals")
        _require_notice(self.notice)

    @property
    def proposal(self) -> FeedbackProposal | None:
        """The first deterministic proposal, convenient for a one-at-a-time CLI."""
        return self.proposals[0] if self.proposals else None


def require_sha256(value: str) -> str:
    """Return a normalized SHA-256 digest or reject malformed provenance."""
    if not isinstance(value, str):
        raise ValueError("config_sha256 must be text")
    digest = value.strip().lower()
    if len(digest) != SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError("config_sha256 must be a 64-character hexadecimal SHA-256")
    return digest


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _bounded(value: str, field_name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty text")
    if len(text) > maximum:
        raise ValueError(f"{field_name} cannot exceed {maximum} characters")
    return text


def _bounded_optional(value: str, field_name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    text = value.strip()
    if len(text) > maximum:
        raise ValueError(f"{field_name} cannot exceed {maximum} characters")
    return text


def _require_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be an exact decimal")


def _event_id(value: str) -> str:
    event_id = _bounded(value, "evidence id", MAX_IDENTIFIER_LENGTH)
    if not event_id.startswith("feedback-") or not _is_hex_digest(
        event_id.removeprefix("feedback-")
    ):
        raise ValueError("evidence id must be a feedback event id")
    return event_id


def _proposal_id(value: str) -> str:
    proposal_id = _bounded(value, "proposal id", MAX_IDENTIFIER_LENGTH)
    if not proposal_id.startswith("proposal-") or not _is_hex_digest(
        proposal_id.removeprefix("proposal-")
    ):
        raise ValueError("proposal id must contain its SHA-256 digest")
    return proposal_id


def _is_hex_digest(value: str) -> bool:
    return len(value) == SHA256_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def _require_notice(value: str) -> None:
    if value != NO_CONFIG_CHANGE_NOTICE:
        raise ValueError(f"notice must say: {NO_CONFIG_CHANGE_NOTICE}")
