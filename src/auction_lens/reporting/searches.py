"""Phrases to paste into the provider's own search bar.

A report links thirty lots one at a time, which is the wrong shape for a person
who wants to look through a whole category at the provider's end and bid there.
The obvious answer -- one query that finds exactly these lots -- is not
available: the provider's search has no OR, so a single phrase cannot cover a
set of unlike titles.

So this answers the question that can be answered. Of the words a rule already
says it wants, which few, asked one after another, cover everything the rule
found today, and how much else does each drag in with it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import InterestRule
from ..models import Candidate, Listing

# Below this a person may as well use the links. The point of a phrase is to
# replace a list too long to click through.
FEWEST_LOTS_WORTH_A_PHRASE = 4

# How many strangers a phrase may surface for each lot it was wanted for.
# "car seat" reaches one straggler and brings thirty-seven other lots with it,
# which is not a shortcut: scrolling that is worse than opening the one link.
# A lot no cheap phrase reaches is simply left to its link.
MOST_STRANGERS_PER_LOT = 8


@dataclass(frozen=True)
class SearchHint:
    """One phrase to paste, and honestly what it will and will not find."""

    rule: str
    phrase: str
    finds: int
    also_finds: int


def search_hints(
    candidates: list[Candidate],
    listings: list[Listing],
    rules: tuple[InterestRule, ...],
) -> tuple[SearchHint, ...]:
    """A short paste-able way to reach each thing the report found a lot of."""
    wanted_by_rule: dict[str, list[Listing]] = {}
    for candidate in candidates:
        if candidate.rule_name:
            wanted_by_rule.setdefault(candidate.rule_name, []).append(candidate.listing)

    hints: list[SearchHint] = []
    for rule in rules:
        found = wanted_by_rule.get(rule.name, [])
        if len(found) < FEWEST_LOTS_WORTH_A_PHRASE:
            continue
        hints.extend(_cover(rule, found, listings))
    return tuple(hints)


def _cover(
    rule: InterestRule, found: list[Listing], listings: list[Listing]
) -> list[SearchHint]:
    """Choose phrases until every lot is reachable by one of them.

    Cheapest first, where cheapest means the most wanted lots per unwanted lot
    the same phrase surfaces. That is the trade a person actually makes when
    they paste one in and look at what comes back.
    """
    wanted_ids = {listing.listing_id for listing in found}
    unreached = set(wanted_ids)
    chosen: list[SearchHint] = []
    while unreached:
        best = _cheapest_phrase(rule, found, listings, wanted_ids, unreached)
        if best is None:
            # Nothing the rule asks for names these, which happens when a lot
            # matched on a word the operator later stopped asking for.
            break
        phrase, reaches = best
        chosen.append(
            SearchHint(
                rule=rule.name,
                phrase=phrase,
                finds=len(reaches),
                also_finds=_noise(phrase, listings, wanted_ids),
            )
        )
        unreached -= reaches
    return chosen


def _cheapest_phrase(
    rule: InterestRule,
    found: list[Listing],
    listings: list[Listing],
    wanted_ids: set[str],
    unreached: set[str],
) -> tuple[str, set[str]] | None:
    best: tuple[float, str, set[str]] | None = None
    for phrase in rule.any_terms:
        reaches = {
            listing.listing_id
            for listing in found
            if listing.listing_id in unreached and phrase in listing.title.lower()
        }
        if not reaches:
            continue
        noise = _noise(phrase, listings, wanted_ids)
        if noise > len(reaches) * MOST_STRANGERS_PER_LOT:
            continue
        value = len(reaches) / (1 + noise)
        if best is None or value > best[0]:
            best = (value, phrase, reaches)
    return None if best is None else (best[1], best[2])


def _noise(phrase: str, listings: list[Listing], wanted_ids: set[str]) -> int:
    """How many lots this phrase surfaces that the report did not want."""
    return sum(
        1
        for listing in listings
        if listing.listing_id not in wanted_ids and phrase in listing.title.lower()
    )
