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
    # Words that settle the question the other way: a lot saying any of these
    # is not the thing, whatever else it says. A broad term has to be broad to
    # find anything, and the cost of that is a known set of other products
    # sold under the same word -- a blood pressure monitor, a baby monitor.
    # Naming them here declines them before anything is scored or asked, which
    # is cheaper than a judgement and, unlike one, always gives the same answer.
    none_terms: tuple[str, ...] = ()
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
    # How late it has to be before an unasked lot's price is worth believing.
    # Separate from ending_soon_minutes, which asks whether to add a bonus to
    # something already worth reporting; this asks whether there is anything
    # to report at all. See anomaly_price_is_tested for the measurement.
    anomaly_within_minutes: int = 120
    # How many days before its closing day a requested lot may be reported.
    # Zero means on its closing day only. Deliberately days and not minutes,
    # unlike the bar above; wanted_is_timely says why, and why that distinction
    # is the whole difference between this and a window removed once already.
    wanted_lead_days: int = 0
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
        require_not_negative(
            self.anomaly_within_minutes, field_name="anomaly_within_minutes"
        )
        require_not_negative(self.wanted_lead_days, field_name="wanted_lead_days")

    def anomaly_price_is_tested(self, minutes_remaining: float | None) -> bool:
        """Whether enough of the clock has run for this price to mean anything.

        An opening bid is not a discount anybody offered. Before bidding, a
        lot's total sits near zero, so its share of stated retail is near zero
        and the discount score is near full marks -- for every lot alike. Of
        3,064 recorded lots seen with no bids at least six hours out, the
        ceiling admitted all 3,064, which is a test that cannot be failed.

        The cure is the clock and not the bid count, which measured backwards:
        across 25,703 readings with a known outcome, requiring any bid at all
        held 72.1% of them still under the ceiling at the close and requiring
        four or more held 64.6%, both worse than the 73.7% of the ungated
        ceiling, because bidding is evidence of demand rather than of
        cheapness. Nearness to the close went the other way -- 81.7% within
        six hours and 85.9% within two -- against a 58.0% base rate for any
        reading at all. Two hours is the default because it is both the
        sharper number and the point where the claim is still actionable.

        This bar does not touch a want. Evidence may retire a bargain claim,
        never a request: an unbid lot somebody asked for is still asked for.
        Whether a request closes near enough to be worth reading is a separate
        question with a separate owner in wanted_is_timely, asked in days
        rather than minutes, and answered by deferring rather than refusing.

        A lot that has already closed answers yes, because its price is as
        tested as a price gets. That it should not be in a report at all is a
        different fact with a different owner, and answering it here would
        hide one question inside another.
        """
        if self.anomaly_within_minutes <= 0:
            return True
        if minutes_remaining is None:
            # No clock to wait for. A listing that never stated an end is not
            # an auction caught mid-flight, and its price is as settled as it
            # will ever be, so the fault this gate exists for cannot apply.
            return True
        return minutes_remaining <= self.anomaly_within_minutes

    def wanted_is_timely(self, days_until_close: int | None) -> bool:
        """Whether a requested lot closes near enough to be worth the page.

        Not the bargain question asked twice. That bar protects a claim about
        price; this one protects the reader's attention. A lot nobody has bid
        on yet says the thing exists and nothing at all about what it costs,
        and a card whose total reads $0.00 is not information.

        Measured over 104,810 readings that also carry a reading near the
        close: a bid read 20 to 30 hours out rose 95% of the time by a median
        30x, and its median share of stated retail went from 0.0% at the
        reading to 15.5% at the close. That final share is where lots land
        whenever they are read -- 14.6% to 17.2% in every band from two hours
        to thirty -- so a share measured a day out reads the clock and not the
        lot. A page built before the closing day fills with them: on one
        evening's capture, 21 of 24 cards, and ten of those carried no bid.

        Deliberately a calendar question and not a clock one. A window counted
        from the moment a run starts was tried and removed, because one number
        meant something different at every hour of the day: measured then, a
        14-hour window cut nothing from an evening run and 794 of 1,406 lots
        from a morning one, and every lot it hid that morning closed that same
        evening. A closing day cannot do that. The same reasoning already
        settled the companion's "is a sweep owed", which is a calendar
        question for the same reason.

        Deferring is not retiring, which is what keeps this consistent with
        the rule above it and with the ranking the removed window was replaced
        by. A lot held back tonight is read on its own closing day, by every
        pass that day, while it is still open: all 95,014 recorded lots
        carrying an end time close between 18:00 and 21:59 local, and the
        first pass of the day runs well before that.

        Zero lead days is the default because the record has no lot closing
        between 22:00 and 18:00, so the midnight boundary falls in an empty
        stretch rather than through the middle of anything. A site that posted
        lots around the clock would need this chosen against its own
        distribution rather than inherited.

        A lot that never stated a closing day answers yes: there is no day to
        wait for, and silence is not a reason to hide something asked for.
        """
        if days_until_close is None:
            return True
        return days_until_close <= self.wanted_lead_days

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
