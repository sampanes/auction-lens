"""The most a lot of one kind is worth bidding, as its operator decided once.

A report used to say what a lot costs now and what it is likely to cost. That
answers "what will this go for", which is a question about other bidders. The
question that decides whether to bid is the other one -- "what is it worth to
me" -- and a person asked it fresh on every card answers it a little higher
each time, because they lost the last one, or this one looks better, or the
retail sticker says it should be worth more. A number written down in advance
does not drift.

So the rubric is configuration: a starting figure, raised by tiers of the size
a title states, lowered by what the grade says is wrong with this copy, and
withdrawn entirely by the faults that make the thing not worth having. It is
printed beside the bid and never moves a score, for the same reason a product
note does not: the score already has its owners, and this is the operator's
own price, not evidence about the lot.

Only a stated size can raise the figure. A title that says nothing gets the
starting figure, which is the honest answer: an unmeasured thing is not a big
one, and the card says the size was not stated so the reader can look.

Some kinds are told apart by model rather than size. Two products of one
size can be worth very different amounts, and the title names the one this
is. A model the title mentions sets the figure
outright, in place of the starting figure and every size tier.

Beside the ceiling a rubric may keep an aim: the price actually hoped for,
which the ceiling is not. The ceiling is the most to bid. The aim is
what the operator expects to pay by waiting for the next copy, so a bid
between them is a sign of patience being lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..values import require_not_negative


@dataclass(frozen=True)
class HammerTier:
    """A higher figure for a lot whose title states it is at least this big.

    Exactly one measurement per tier, so a tier means one thing. Two tiers that
    agree -- 15 feet or 345 pounds, both worth the same -- are written as two
    rows rather than one row with an "or" in it.
    """

    hammer: Decimal
    feet: Decimal | None = None
    pounds: Decimal | None = None

    def __post_init__(self) -> None:
        require_not_negative(self.hammer, field_name="hammer")
        if (self.feet is None) == (self.pounds is None):
            raise ValueError("a max_hammer tier needs exactly one of feet or pounds")
        for name in ("feet", "pounds"):
            value = getattr(self, name)
            if value is not None:
                require_not_negative(value, field_name=name)


@dataclass(frozen=True)
class HammerModel:
    """A figure of its own for a product that titles name by model.

    `terms` are matched as whole words in the title, so "zx-5" does not
    match "zx-500". A rubric tries its models in the order written and the
    first one mentioned wins, so a model whose name contains another's goes
    first.
    """

    name: str
    terms: tuple[str, ...]
    hammer: Decimal
    aim: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a max_hammer model needs a name")
        if not self.terms:
            raise ValueError("a max_hammer model needs at least one term")
        require_not_negative(self.hammer, field_name="hammer")
        _require_aim_within(self.aim, self.hammer)


@dataclass(frozen=True)
class HammerRubric:
    """What one interest's lots are worth bidding, before premium and tax.

    `base` is the figure for a lot no tier lifts, including one whose title
    states no size at all. The highest tier the title reaches replaces it.

    The three condition instruments are deliberately different shapes, because
    the faults they describe are. `less` is a discount for a copy that is fine
    but not new. `at_most` is a cap for a copy nobody has checked, whose figure
    should be low whatever its size. `walk` is a phrase that makes the lot not
    worth any figure -- damage, a missing blower -- and is read in the title,
    the warehouse's note and the grade alike, since a warehouse writes those
    faults wherever it likes.

    `less` and `at_most` are keyed by condition label, exactly as condition
    penalties are, so that "used" here is the same word a profile penalises.

    `aim` is the hoped-for price of a lot no model names; each model carries
    its own.
    """

    base: Decimal
    tiers: tuple[HammerTier, ...] = ()
    less: dict[str, Decimal] = field(default_factory=dict)
    at_most: dict[str, Decimal] = field(default_factory=dict)
    walk: tuple[str, ...] = ()
    # A far branch turns a cheap lot into an errand. Off unless asked for,
    # because the location bars already decide whether a far lot is shown.
    walk_if_far: bool = False
    models: tuple[HammerModel, ...] = ()
    aim: Decimal | None = None

    def __post_init__(self) -> None:
        require_not_negative(self.base, field_name="base")
        _require_aim_within(self.aim, self.base)
        for table in ("less", "at_most"):
            for label, amount in getattr(self, table).items():
                require_not_negative(amount, field_name=f"{table}.{label}")


def _require_aim_within(aim: Decimal | None, ceiling: Decimal) -> None:
    """An aim above its own ceiling is a typo, not a plan."""
    if aim is None:
        return
    require_not_negative(aim, field_name="aim")
    if aim > ceiling:
        raise ValueError(f"aim ${aim} is above its ceiling ${ceiling}")
