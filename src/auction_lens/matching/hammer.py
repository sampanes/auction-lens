"""Read a lot's stated size and turn its rule's rubric into one figure.

The rubric belongs to the operator (config/hammer.py); this is only the
arithmetic, plus the one thing the rubric cannot do for itself, which is read
how big a title says the thing is.

Sizes are read only where a unit says what the number is. "12.4x11FT" and
"130 x 110 x 80 in" are measurements; the 10 in "10-in-1" and the 9 in
"Ages 3-9" are not, and reading them as one would invent a size. A number
with no unit is left alone, which is the right way to be wrong here: a missed
size leaves the starting figure and a card saying so, while an invented one
raises a ceiling nobody chose.

A model named in the title is looked for before any size, and when one is
found the size does not count: the operator priced that product, not its
dimensions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ..config.hammer import HammerModel, HammerRubric, HammerTier
from ..listings.model import Listing
from .text import mentions

INCHES_PER_FOOT = Decimal(12)

NUMBER = r"\d+(?:\.\d+)?"
FEET = r"(?:ft|feet|foot|')"
# "in" is a unit only when no number follows it: "102 in" is a length and
# "10 in 1" is how many toys are in the set.
INCHES = r"(?:inch(?:es)?|in\b(?!\s*\d)|\"|'')"
# A side may be labelled where it is written: "14.4ft L x 9.1ft W".
SIDE = r"(?:\s*\(?[lwh]\b\)?)?"
# The separator between sides, including the multiplication sign itself.
BY = r"\s*[x*\u00d7]\s*"
# Several sides sharing the unit written after the last of them.
# Inches are tried first so that two apostrophes are not read as feet.
UNIT = rf"(?:{INCHES}|{FEET})"
SIDES = re.compile(rf"((?:{NUMBER}\s*{UNIT}?{SIDE}{BY})+{NUMBER})\s*({UNIT})")
# One side carrying its own unit.
ONE_SIDE = re.compile(rf"(?<![\d.])({NUMBER})\s*({UNIT})")
POUNDS = re.compile(rf"(?<![\d.])({NUMBER})\s*(?:lbs?|pounds?)\b")


@dataclass(frozen=True)
class Measured:
    """The largest side and the largest weight a title states, if it does."""

    feet: Decimal | None = None
    pounds: Decimal | None = None

    @property
    def is_stated(self) -> bool:
        return self.feet is not None or self.pounds is not None


@dataclass(frozen=True)
class HammerCeiling:
    """The most to bid on one lot, and every step that produced the figure.

    `hammer` is None when the lot is not worth bidding at all; `walk` then
    says why. The steps are kept so a card can show its working: a figure
    nobody can check is a figure nobody trusts at the moment it matters.
    `basis` is what the figure was read from -- a model, a size, or a size
    the title did not state -- and is empty for a rubric that reads neither.
    """

    hammer: Decimal | None
    measured: Measured
    adjustments: tuple[str, ...] = ()
    walk: str = ""
    basis: str = ""
    aim: Decimal | None = None


def measured_in(text: str) -> Measured:
    """The longest side in feet and the largest weight a text states."""
    lowered = text.lower()
    feet = [_in_feet(number, unit) for number, unit in _sides(lowered)]
    pounds = [Decimal(number) for number in POUNDS.findall(lowered)]
    return Measured(
        feet=max(feet) if feet else None,
        pounds=max(pounds) if pounds else None,
    )


def ceiling_for(
    listing: Listing,
    conditions: frozenset[str],
    rubric: HammerRubric,
    *,
    is_far: bool,
) -> HammerCeiling:
    """Apply one rubric to one lot: the model or size, then what is wrong with it."""
    measured = measured_in(listing.title)
    walk = _walk_reason(listing, conditions, rubric, is_far=is_far)
    if walk:
        return HammerCeiling(hammer=None, measured=measured, walk=walk)

    model = _named_model(listing.title, rubric.models)
    if model is not None:
        figure, aim, basis = model.hammer, model.aim, model.name
    else:
        reached = [tier.hammer for tier in rubric.tiers if _reaches(measured, tier)]
        figure, aim = max([rubric.base, *reached]), rubric.aim
        basis = _size_read(measured) if rubric.tiers else ""
    adjustments = []
    for label in sorted(conditions & rubric.less.keys()):
        amount = rubric.less[label]
        figure -= amount
        adjustments.append(f"{label} -${amount:,.0f}")
    for label in sorted(conditions & rubric.at_most.keys()):
        cap = rubric.at_most[label]
        if cap < figure:
            figure = cap
            adjustments.append(f"{label}: at most ${cap:,.0f}")
    figure = max(figure, Decimal(0))
    return HammerCeiling(
        hammer=figure,
        measured=measured,
        adjustments=tuple(adjustments),
        basis=basis,
        # An aim at or past what this copy may fetch says nothing the
        # ceiling does not already say.
        aim=aim if aim is not None and aim < figure else None,
    )


def _named_model(title: str, models: tuple[HammerModel, ...]) -> HammerModel | None:
    """The first model, in the order written, that the title names."""
    lowered = title.lower()
    return next(
        (model for model in models if any(mentions(lowered, term) for term in model.terms)),
        None,
    )


def _walk_reason(
    listing: Listing, conditions: frozenset[str], rubric: HammerRubric, *, is_far: bool
) -> str:
    """The first fault that makes this lot not worth any bid, or "" if none.

    The warehouse's note is read as well as the title, because "blower not
    included" is the kind of thing it writes there and nowhere else. The
    location is not: a branch named "Damage Outlet" would otherwise walk every
    lot it holds.
    """
    if rubric.walk_if_far and is_far:
        return "far branch"
    text = " ".join((listing.title, listing.notes, *sorted(conditions))).lower()
    return next((phrase for phrase in rubric.walk if mentions(text, phrase)), "")


def _reaches(measured: Measured, tier: HammerTier) -> bool:
    if tier.feet is not None:
        return measured.feet is not None and measured.feet >= tier.feet
    return measured.pounds is not None and measured.pounds >= tier.pounds


def _sides(text: str) -> list[tuple[str, str]]:
    """Every (number, unit) pair, with a shared unit spread across its sides."""
    found = []
    for group, unit in SIDES.findall(text):
        found.extend((number, unit) for number in re.findall(NUMBER, group))
    found.extend(ONE_SIDE.findall(text))
    return found


def _size_read(measured: Measured) -> str:
    if not measured.is_stated:
        return "size not stated"
    parts = []
    if measured.feet is not None:
        parts.append(f"{_tenths(measured.feet)} ft")
    if measured.pounds is not None:
        parts.append(f"{_tenths(measured.pounds)} lb")
    return ", ".join(parts)


def _tenths(value: Decimal) -> str:
    return f"{value:.1f}".removesuffix(".0")


def _in_feet(number: str, unit: str) -> Decimal:
    value = Decimal(number)
    if unit in ("'", "ft", "feet", "foot"):
        return value
    return value / INCHES_PER_FOOT
