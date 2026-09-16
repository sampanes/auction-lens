"""Whether a garment a listing describes is one a particular person can wear.

Clothing is the one category where the right product at the right price is
still useless. A "Milwaukee Leather Men's Hooded Riding Shirt - Large" is a
fine shirt and the wrong shirt, and no amount of judging the product tells you
that, because the mismatch is not with the lot but with the reader.

So this reads the size and the audience out of the title and compares them
with one person. Three deliberate limits keep it honest:

A size the title never states cannot disqualify anything. Titles are written
by a warehouse, not a catalogue, and many say nothing about size at all;
refusing those would hide more real finds than it saves. The same reasoning
governs the per-rule retail ceiling -- only a claim that was actually made can
be checked.

A size chart nobody filled in cannot disqualify anything either. Shirts,
shoes and trousers are measured three unrelated ways, and somebody who wrote
down a shirt size has not thereby said their waist is wrong. So the halves are
compared chart by chart: a stated size is only ever checked against a declared
size of the same kind.

A size is only read where it is unambiguous. "Large" is a size in a clause of
its own and an adjective in "Large Floor Squeegee", so a clause counts only
when the whole of it is a size.
A bare "9" is a shoe size, a quantity, a model number and a screen measurement,
so it counts only where the title says "size" next to it.
"""

from __future__ import annotations

import re

from .model import Person

# Letter sizes and the words people write instead of them. Each maps to the
# one spelling a person's configured list is compared against, so "XL",
# "X-Large" and "extra large" are the same answer to the same question.
LETTER_SIZES = {
    "xs": "xs",
    "x-small": "xs",
    "xsmall": "xs",
    "extra small": "xs",
    "s": "s",
    "small": "s",
    "m": "m",
    "med": "m",
    "medium": "m",
    "l": "l",
    "large": "l",
    "xl": "xl",
    "x-large": "xl",
    "xlarge": "xl",
    "extra large": "xl",
    "xxl": "xxl",
    "2xl": "xxl",
    "2x": "xxl",
    "xx-large": "xxl",
    "xxxl": "xxxl",
    "3xl": "xxxl",
    "3x": "xxxl",
}

# Who a garment is cut for. A person who wears men's and unisex is refused a
# women's jacket even in the right size, which is the other half of the
# question and the half a size chart cannot answer.
STYLES = {
    "men": "men",
    "mens": "men",
    "man": "men",
    "male": "men",
    "women": "women",
    "womens": "women",
    "woman": "women",
    "ladies": "women",
    "female": "women",
    "boy": "boys",
    "boys": "boys",
    "girl": "girls",
    "girls": "girls",
    "kid": "kids",
    "kids": "kids",
    "youth": "kids",
    "toddler": "toddler",
    "infant": "baby",
    "baby": "baby",
    "newborn": "baby",
    "unisex": "unisex",
}

# The three size charts, kept apart because they measure unrelated things.
# A person who declared a shirt size said nothing about their shoes.
LETTER = "letter"
NUMBER = "number"
WAIST = "waist"

# A bare number is only a size where the title says so. "Size 9" is a nine;
# the 9 in "9 Piece Set", "9 inch" and "Model 9000" is not. The same label is
# how a letter size gets stated mid-title: "Size M", "size: Large".
# Tried longest-first so "9 1/2" is not read as a nine, and so a letter size
# word is only reached once both numeric spellings have failed.
LABELLED_SIZE = re.compile(
    r"\bsizes?\s*:?\s*(\d{1,2}\s*1/2|\d{1,2}(?:\.5)?|[a-z][a-z-]{0,10})\b"
)
# The other place a number is unambiguous, because nothing else is written
# this way -- except that a sink and a gazebo are, so a pair of numbers
# carrying a unit is a measurement rather than a waist.
WAIST_BY_INSEAM = re.compile(
    r"\b(\d{2}\s*x\s*\d{2})\b(?!\s*(?:inch|in\b|ft|feet|foot|cm|mm|\"|'))"
)
# What separates one clause of a warehouse title from the next. A size stands
# alone in its own clause; an adjective never does.
CLAUSE = re.compile(r"[,()|]|\s-\s|\s+-\s*$")
NUMERIC_SIZE = re.compile(r"^\d{1,2}(\.5)?$")


def sizes_in(text: str) -> dict[str, frozenset[str]]:
    """Every size the text states, grouped by which chart it belongs to.

    An empty result means the title said nothing about size, which is a
    different answer from "said a size this person does not wear" and is
    treated differently by :func:`fits`.
    """
    found: set[str] = set()
    for written in LABELLED_SIZE.findall(text):
        canonical = canonical_size(written)
        if canonical:
            found.add(canonical)
    found.update(match.replace(" ", "") for match in WAIST_BY_INSEAM.findall(text))
    found.update(_sizes_standing_alone(text))
    return _by_chart(found)


def styles_in(text: str) -> frozenset[str]:
    """Every audience the text names, such as men's, girls', or unisex."""
    return frozenset(
        canonical for spelling, canonical in STYLES.items() if says(text, spelling)
    )


def fits(text: str, person: Person) -> bool:
    """Whether this person could wear what the text describes.

    Silence passes, on either side and chart by chart. The only refusal is a
    title that states something and a person who declared the same kind of
    thing and does not have it.
    """
    worn = _by_chart(person.sizes)
    for chart, stated in sizes_in(text).items():
        if chart in worn and not stated & worn[chart]:
            return False
    return _no_conflict(styles_in(text), person.styles)


def canonical_size(written: str) -> str:
    """One spelling per size, so "Medium", "M" and "9 1/2" each mean one thing.

    Returns an empty string for a word that is not a size at all, which is how
    a labelled match like "size chart" is discarded rather than believed.
    """
    tidy = written.strip().lower()
    if tidy in LETTER_SIZES:
        return LETTER_SIZES[tidy]
    numeric = tidy.replace(" ", "").replace("1/2", ".5")
    if NUMERIC_SIZE.match(numeric) or WAIST_BY_INSEAM.fullmatch(numeric):
        return numeric
    return ""


def says(text: str, word: str) -> bool:
    """Whether the text uses the word on its own rather than inside another.

    Its own test rather than a call to the shared term matcher because that one
    reads a trailing "s" as a plural. Here "mens" and "men" must both land on
    the one canonical answer, so the boundary is what matters rather than the
    ending.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None


def _by_chart(sizes: frozenset[str] | set[str]) -> dict[str, frozenset[str]]:
    """Sort written sizes into the three charts, dropping anything unreadable."""
    charts: dict[str, set[str]] = {}
    for size in sizes:
        canonical = canonical_size(size)
        if canonical:
            charts.setdefault(_chart_of(canonical), set()).add(canonical)
    return {chart: frozenset(values) for chart, values in charts.items()}


def _chart_of(canonical: str) -> str:
    """Which of the three size charts one canonical size is measured on.

    Asked of the waist pattern rather than by looking for an "x", because
    "xl" has one and is not a pair of trousers.
    """
    if WAIST_BY_INSEAM.fullmatch(canonical):
        return WAIST
    return NUMBER if NUMERIC_SIZE.match(canonical) else LETTER


def _no_conflict(stated: frozenset[str], worn: frozenset[str]) -> bool:
    """Whether what the title said leaves this person's list still possible.

    Three cases, and only the last is a refusal: the title said nothing, the
    person declared nothing, or the title named something and none of it was
    on the person's list.
    """
    if not stated or not worn:
        return True
    return bool(stated & worn)


def _sizes_standing_alone(text: str) -> set[str]:
    """Letter sizes written without the word "size" in front of them.

    A size word alone is not enough. "Large Floor Squeegee" and "Extra Large
    Inflatable Pool" say large about a thing that has no size chart, and a
    reader that believes them refuses a medium jacket for saying "large front
    pocket". The adjective and the size look identical; what separates them is
    that a size gets a clause to itself.

    So a warehouse title is cut at its commas, brackets and spaced dashes, and
    a clause counts only when the whole of it is a size. That misses a size
    buried mid-sentence, which is the right way to be wrong here: a missed
    size shows a lot that can be eyeballed, while an invented one hides a real
    find and says nothing.
    """
    found = set()
    for clause in CLAUSE.split(text):
        canonical = LETTER_SIZES.get(clause.strip())
        if canonical:
            found.add(canonical)
    return found
