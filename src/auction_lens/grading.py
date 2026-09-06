"""How a provider's condition grades become tags a person can skim.

A lot is graded on several separate axes, and each is answered with a word. The
words do not carry their own meaning: "Yes" is good news about packaging and bad
news about assembly. That polarity lives in one table below, so nothing
downstream has to remember which way round an axis reads.

Two things this module deliberately does differently from the provider's own
page. It says so when an axis was not answered, because "nobody checked whether
parts are missing" is a risk a bidder should see rather than a blank space. And
it keeps the axis beside the answer, so a report can say which question a red
tag is answering.

The vocabulary was read off real listings on 2026-09-06; see
docs/DATA_ACQUISITION.md for how, and fixtures/nellis/product-grade-samples.json
for the samples.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .fields import require_within

# A quality rating is out of five, and is the provider's own opinion. It is not
# a summary of the tags: a used lot with nothing else wrong still rates five.
LOWEST_RATING = 1
HIGHEST_RATING = 5

# The word a provider uses when an axis was never answered.
UNANSWERED = "unknown"


class Tag(StrEnum):
    """The colour an answer reads as at a glance."""

    GREEN = "green"
    AMBER = "amber"
    RED = "red"


@dataclass(frozen=True)
class Answer:
    """What one answer on one axis means to the person reading it."""

    label: str
    tag: Tag


@dataclass(frozen=True)
class Axis:
    """One thing a provider grades, and what each of its answers means."""

    name: str
    unanswered_label: str
    answers: dict[str, Answer]

    def read(self, written: str) -> ConditionTag:
        """Turn one written answer into a tag, naming the axis it answers."""
        answer = self.answers.get(written.strip().lower())
        if answer is None:
            return ConditionTag(self.name, self.unanswered_label, Tag.AMBER)
        return ConditionTag(self.name, answer.label, answer.tag)


@dataclass(frozen=True)
class ConditionTag:
    """One graded axis, as the provider answered it, ready to be shown."""

    axis: str
    label: str
    tag: Tag

    @property
    def is_concerning(self) -> bool:
        """Anything not green is worth a bidder's attention."""
        return self.tag != Tag.GREEN


# In the order a person reads them, worst-news axes first.
AXES = (
    Axis(
        "condition",
        "Condition Unknown",
        {
            "new": Answer("New", Tag.GREEN),
            "open box": Answer("Open Box", Tag.AMBER),
            "used": Answer("Used", Tag.RED),
        },
    ),
    Axis(
        "functional",
        "Function Untested",
        {
            "yes": Answer("Functional", Tag.GREEN),
            "untested": Answer("Untested", Tag.RED),
            "no": Answer("Not Functional", Tag.RED),
        },
    ),
    Axis(
        "damage",
        "Damage Unknown",
        {
            "none": Answer("No Damage", Tag.GREEN),
            "minor": Answer("Minor Damage", Tag.RED),
            "major": Answer("Major Damage", Tag.RED),
        },
    ),
    Axis(
        "missing_parts",
        "Missing Parts Unknown",
        {
            "no": Answer("No Missing Parts", Tag.GREEN),
            "yes": Answer("Missing Parts", Tag.RED),
        },
    ),
    Axis(
        "assembly",
        "Assembly Unknown",
        {
            "no": Answer("No Assembly Needed", Tag.GREEN),
            "yes": Answer("Assembly Required", Tag.RED),
        },
    ),
    Axis(
        "package",
        "Packaging Unknown",
        {
            "yes": Answer("In Package", Tag.GREEN),
            "no": Answer("No Package", Tag.RED),
        },
    ),
)

AXES_BY_NAME = {axis.name: axis for axis in AXES}


@dataclass(frozen=True)
class Grade:
    """Every axis a provider answered about one lot, plus its own rating."""

    tags: tuple[ConditionTag, ...] = ()
    rating: int | None = None

    def __post_init__(self) -> None:
        if self.rating is not None:
            require_within(
                self.rating,
                low=LOWEST_RATING,
                high=HIGHEST_RATING,
                field_name="rating",
            )

    @property
    def concerns(self) -> tuple[ConditionTag, ...]:
        """The tags that are not green, which is what a report leads with."""
        return tuple(tag for tag in self.tags if tag.is_concerning)

    @property
    def words(self) -> tuple[str, ...]:
        """The labels in the form scoring matches conditions on: lowercased.

        Scoring already knows how to penalise words like "missing parts", and a
        grade is just a stricter way of arriving at the same vocabulary.
        """
        return tuple(tag.label.lower() for tag in self.concerns)


def read_grade(answers: Mapping[str, Any] | None, rating: Any = None) -> Grade | None:
    """Read the graded axes a listing file recorded, in the order AXES lists.

    An axis this provider does not grade at all is simply absent. An axis it
    grades but could not answer becomes an amber tag, because a bidder cannot
    otherwise tell a lot that is clean from one nobody checked.
    """
    if not answers:
        return None
    graded = tuple(axis for axis in AXES if axis.name in answers)
    tags = tuple(axis.read(str(answers[axis.name])) for axis in graded)
    return Grade(tags=tags, rating=None if rating is None else int(rating))
