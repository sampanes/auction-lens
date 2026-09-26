"""The interests, conditions, and score bars that decide what is useful."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from ..matching.model import HIGHEST_SCORE, LOWEST_SCORE, Person
from ..values import (
    HIGHEST_RATE,
    require_at_least,
    require_at_most,
    require_not_negative,
    require_rate,
    require_within,
)


@dataclass(frozen=True)
class ConditionPolicy:
    """Condition rules scoped to one interest, and therefore to one intended use."""

    reject: frozenset[str] = frozenset()
    penalties: dict[str, int] = field(default_factory=dict)
    allow_unknown: bool = True


@dataclass(frozen=True)
class InterestRule:
    """One reason a listing would be useful, with the conditions that use allows."""

    name: str
    # A stable machine-facing identity lets the display name improve without
    # severing outcomes already recorded against the rule. Existing configs
    # need no migration: an omitted id settles to the original name.
    interest_id: str = ""
    purpose: str = "use"
    any_terms: tuple[str, ...] = ()
    all_terms: tuple[str, ...] = ()
    # What this interest is actually after, in a sentence. The terms above
    # only have to be inclusive enough to find candidates; this is what
    # separates the thing from everything else wearing its name.
    wants: str = ""
    # Words that name an accessory, checked only where they sit beside one of
    # the terms above. Inherited from [interest_defaults]; see InterestDefaults.
    accessory_nouns: tuple[str, ...] = ()
    max_total_cost: Decimal | None = None
    # A value floor separates the thing from accessories that borrow its name.
    minimum_retail: Decimal | None = None
    # A per-interest price ratio can narrow, but never widen, the shared ceiling.
    maximum_retail_ratio: Decimal | None = None
    # Who this has to fit, for the wants where the right product in the wrong
    # size is not a find. Named as `fits = "someone"` in TOML and resolved to
    # the person here at load time, so matching never needs the whole config.
    fits: Person | None = None
    # None is ongoing; a positive count plus a stable id can retire after wins.
    wanted: int | None = None
    minimum_score: int = 0
    # Weight ranks admitted matches and cannot smuggle a lot past a score bar.
    weight: Decimal = Decimal("1")
    condition_profile: str = ""
    condition: ConditionPolicy = field(default_factory=ConditionPolicy)

    def __post_init__(self) -> None:
        if self.wanted is not None:
            if isinstance(self.wanted, bool) or not isinstance(self.wanted, int):
                raise ValueError("wanted must be a whole number")
            require_at_least(self.wanted, 1, field_name="wanted")
            if not self.interest_id.strip():
                raise ValueError("finite interests require an explicit stable id")
        effective_id = self.interest_id.strip() or self.name
        object.__setattr__(self, "interest_id", effective_id)
        require_within(
            self.minimum_score,
            low=LOWEST_SCORE,
            high=HIGHEST_SCORE,
            field_name="minimum_score",
        )
        require_not_negative(self.weight, field_name="weight")
        if self.max_total_cost is not None:
            require_not_negative(self.max_total_cost, field_name="max_total_cost")
        if self.minimum_retail is not None:
            require_not_negative(self.minimum_retail, field_name="minimum_retail")
        if self.maximum_retail_ratio is not None:
            require_not_negative(
                self.maximum_retail_ratio, field_name="maximum_retail_ratio"
            )
            require_at_most(
                self.maximum_retail_ratio,
                HIGHEST_RATE,
                field_name="maximum_retail_ratio",
            )


@dataclass(frozen=True)
class InterestDefaults:
    """Accessory words shared by every interest rule."""

    accessory_nouns: tuple[str, ...] = ()

    def applied_to(self, rule: InterestRule) -> InterestRule:
        """Apply shared words except those the rule explicitly asks for."""
        asked_for = " ".join(rule.any_terms + rule.all_terms)
        return replace(
            rule,
            accessory_nouns=tuple(
                noun for noun in self.accessory_nouns if noun not in asked_for
            ),
        )


@dataclass(frozen=True)
class ScoringConfig:
    """The bar a listing has to clear, and what counts against it."""

    anomaly_minimum_retail: Decimal = Decimal("100")
    anomaly_maximum_ratio: Decimal = Decimal("0.20")
    # A second, gentler band for lots big enough to deserve one. Unset means
    # there is only the one band, which is how this behaved before.
    large_lot_minimum_retail: Decimal | None = None
    large_lot_maximum_ratio: Decimal = Decimal("0.40")
    # A bargain nobody requested ranks below a fair price on something wanted.
    anomaly_weight: Decimal = Decimal("0.4")
    minimum_report_score: int = 70
    ending_soon_minutes: int = 20
    # The most of stated retail worth paying at auction for anything.
    maximum_retail_ratio: Decimal | None = None
    condition_penalties: dict[str, int] = field(default_factory=dict)
    rejected_conditions: frozenset[str] = frozenset()
    anomaly_condition: ConditionPolicy = field(default_factory=ConditionPolicy)

    def __post_init__(self) -> None:
        require_within(
            self.minimum_report_score,
            low=LOWEST_SCORE,
            high=HIGHEST_SCORE,
            field_name="minimum_report_score",
        )
        require_not_negative(
            self.anomaly_minimum_retail, field_name="anomaly_minimum_retail"
        )
        require_rate(self.anomaly_maximum_ratio, field_name="anomaly_maximum_ratio")
        require_rate(
            self.large_lot_maximum_ratio, field_name="large_lot_maximum_ratio"
        )
        if self.large_lot_minimum_retail is not None:
            require_not_negative(
                self.large_lot_minimum_retail,
                field_name="large_lot_minimum_retail",
            )
            require_at_least(
                self.large_lot_minimum_retail,
                self.anomaly_minimum_retail,
                field_name="large_lot_minimum_retail",
            )
            require_at_least(
                self.large_lot_maximum_ratio,
                self.anomaly_maximum_ratio,
                field_name="large_lot_maximum_ratio",
            )
        if self.maximum_retail_ratio is not None:
            require_rate(self.maximum_retail_ratio, field_name="maximum_retail_ratio")
        require_not_negative(self.anomaly_weight, field_name="anomaly_weight")
        require_not_negative(self.ending_soon_minutes, field_name="ending_soon_minutes")

    def is_large_lot(self, retail: Decimal) -> bool:
        """Whether this lot is big enough for the second, gentler band."""
        return (
            self.large_lot_minimum_retail is not None
            and retail >= self.large_lot_minimum_retail
        )

    def anomaly_ceiling(self, retail: Decimal) -> Decimal | None:
        """The most of stated retail worth paying for a lot nobody asked for.

        One question, answered on a schedule rather than at a single point:
        the larger the thing, the more of its retail is worth paying. A small
        lot has to be a steal before it earns a place in the report; a large
        one is worth seeing at a merely fair price, because the money saved is
        what matters and not the percentage.

        None means a lot this small is not worth reporting unasked at all.
        """
        if self.is_large_lot(retail):
            return self.large_lot_maximum_ratio
        if retail < self.anomaly_minimum_retail:
            return None
        return self.anomaly_maximum_ratio
