"""Match listings to interests and hold the facts every match shares.

An interest says why an item would be useful, so a match starts from a high
score and is reduced only by conditions that the stated purpose cares about.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..config.schema import ConditionPolicy, InterestRule
from ..listings.model import Listing, ObservationChange
from ..matching.text import first_mention, mentions, standalone_mentions
from .logistics import LogisticsAssessment
from .model import (
    BASE_INTEREST_SCORE,
    HIGHEST_SCORE,
    LOWEST_SCORE,
    Candidate,
    CandidateCategory,
)


@dataclass(frozen=True)
class ScoringContext:
    """Cost, condition, timing, and handling facts shared by every matcher."""

    listing: Listing
    conditions: frozenset[str]
    total_cost: Decimal
    change: ObservationChange
    logistics: LogisticsAssessment
    baseline_penalty: int
    ending_soon_bonus: int

    @property
    def is_ending_soon(self) -> bool:
        return self.ending_soon_bonus > 0

    @property
    def score_bonus(self) -> int:
        """Urgency changes quality; observation novelty only changes order."""
        return self.ending_soon_bonus

    @property
    def retail_ratio(self) -> Decimal | None:
        retail = self.listing.estimated_retail
        return None if not retail else self.total_cost / retail

    def candidate(
        self,
        *,
        category: CandidateCategory,
        rule_id: str,
        rule_name: str,
        score: int,
        reasons: Sequence[str],
        weight: Decimal = Decimal("1"),
    ) -> Candidate:
        """Attach one match to the shared facts about this listing."""
        return Candidate(
            listing=self.listing,
            category=category,
            rule_id=rule_id,
            rule_name=rule_name,
            score=score,
            total_cost=self.total_cost,
            retail_ratio=self.retail_ratio,
            reasons=tuple(reasons),
            change=self.change,
            logistics=self.logistics,
            weight=weight,
        )


def policy_admits(conditions: Iterable[str], policy: ConditionPolicy) -> bool:
    """Whether one interest's condition policy accepts these labels."""
    labels = frozenset(conditions)
    if labels & policy.reject:
        return False
    return bool(labels) or policy.allow_unknown


def penalty_for(conditions: Iterable[str], penalties: Mapping[str, int]) -> int:
    """Sum the configured penalty for every condition label present."""
    return sum(penalties.get(label, 0) for label in conditions)


def clamp_score(value: int) -> int:
    """Keep every matching path on the same 0-100 scale."""
    return max(LOWEST_SCORE, min(HIGHEST_SCORE, value))

# The word an accessory uses to name what it fits. Spaced so that it is the
# whole word: "for" and not the tail of "comfort".
HOST_MARKER = " for "

# What a number is counting when "for" is measuring rather than attaching.
# "Tent for 6 Person" says how big the tent is; "Case for 6 or 12-String" says
# what the case fits. Only a word from this list turns a number into a size.
CAPACITY_WORDS = frozenset(
    {
        "person", "people", "adult", "kid", "child", "children", "seat",
        "gallon", "gal", "quart", "qt", "liter", "litre", "cup", "oz", "lb",
        "pack", "count", "piece", "pc", "pcs", "set",
    }
)

# How far from the wanted word an accessory word still counts as attached to it.
ACCESSORY_WORD_GAP = 2

# Titles end words with these; they are not part of the word.
TITLE_PUNCTUATION = ".,:;!?()[]{}\"'-*/"

# How much text around a wanted word can hold ACCESSORY_WORD_GAP words. Two
# words are never sixty characters, and reading the whole title on both sides
# of every match is work with no answer in it.
NEARBY_TEXT = 60


def score_interests(
    context: ScoringContext,
    rules: tuple[InterestRule, ...],
    minimum_report_score: int,
) -> list[Candidate]:
    """Return one candidate per interest rule this listing satisfies."""
    matches = []
    for rule in rules:
        candidate = _score_rule(context, rule, minimum_report_score)
        if candidate is not None:
            matches.append(candidate)
    return matches


def _score_rule(
    context: ScoringContext,
    rule: InterestRule,
    minimum_report_score: int,
) -> Candidate | None:
    if not matches_terms(context.listing, context.total_cost, rule):
        return None
    if not policy_admits(context.conditions, rule.condition):
        return None
    rule_penalty = penalty_for(context.conditions, rule.condition.penalties)
    penalty = context.baseline_penalty + rule_penalty
    score = clamp_score(BASE_INTEREST_SCORE + context.score_bonus - penalty)
    if score < max(rule.minimum_score, minimum_report_score):
        return None
    reasons = [f"matches {rule.purpose} interest '{rule.name}'"]
    if context.is_ending_soon:
        reasons.append("ending soon")
    return context.candidate(
        category=CandidateCategory.WANTED,
        rule_id=rule.interest_id,
        rule_name=rule.name,
        score=score,
        reasons=reasons,
        weight=rule.weight,
    )


def matches_terms(listing: Listing, total_cost: Decimal, rule: InterestRule) -> bool:
    """Apply one rule's term filters, its value floor, and its cost ceiling."""
    searchable = listing.searchable_text
    if rule.any_terms and not any(mentions(searchable, term) for term in rule.any_terms):
        return False
    if rule.all_terms and not all(mentions(searchable, term) for term in rule.all_terms):
        return False
    if describes_an_accessory(searchable, rule):
        return False
    if not _worth_at_least(listing, rule.minimum_retail):
        return False
    if _asks_too_much_of_retail(listing, total_cost, rule.maximum_retail_ratio):
        return False
    return rule.max_total_cost is None or total_cost <= rule.max_total_cost


def describes_an_accessory(searchable: str, rule: InterestRule) -> bool:
    """Whether the lot names the wanted thing without being it.

    An accessory is sold by the name of what it attaches to, so a guitar stand,
    a monitor mount and a battery for a drill all say the wanted word as loudly
    as the real article. The value floor was the first answer to this and it is
    not enough on its own: a set of guitar hangers can retail for more than the
    floor a cheap guitar has to clear.

    Two things separate the accessory from the article, and both are about where
    the words sit rather than which words they are.
    """
    return _named_after_its_host(searchable, rule) or _sold_as_a_fitting(
        searchable, rule
    )


def _named_after_its_host(searchable: str, rule: InterestRule) -> bool:
    """Whether the wanted words appear only after the word "for".

    "Weed Wacker for DeWalt Battery" attaches to a DeWalt; "DeWalt Miter Saw"
    is one. Which side of "for" the wanted word falls on is the whole
    difference: an accessory names its host after it, and an article names
    itself first. So "Electric Bike for Adults" stays -- it is an electric bike
    that happens to say who it is for, and its wanted words come first.

    A word appearing on both sides counts as the earlier one, which keeps a lot
    in the report rather than out of it.
    """
    host_marker = _where_a_host_is_named(searchable)
    if host_marker == -1:
        return False
    # A rule that named no wanted words has nothing here to be positioned, and
    # an empty "all of them are late" would otherwise be vacuously true.
    named = [first_mention(searchable, term) for term in rule.any_terms]
    said = [at for at in named if at != -1]
    return bool(said) and all(at > host_marker for at in said)


def _where_a_host_is_named(searchable: str) -> int:
    """Where "for" introduces a host product, or -1 if it never does.

    Not every "for" names a host. "Tent for 6 Person" and "Stand for 55-75
    Gallon Tanks" are saying how big the thing is, and a title that repeats
    itself -- "Camping Tent for 6 Person, Instant Cabin Tents with Rainfly" --
    then has its wanted word only after a "for" that never introduced anything.
    Read literally that makes a tent an accessory to itself.

    A number alone is not the tell, and trying it that way let a guitar case
    through: "Hard-Shell Case for 6 or 12-String Acoustic Guitar" starts with a
    digit too. What separates them is the word after the number. "6 Person" is
    a capacity and "6 or 12-String" is a specification of the host, so only a
    number followed by a word for how much is skipped, and the next "for" is
    the one worth asking about.
    """
    found = searchable.find(HOST_MARKER)
    while found != -1:
        after = searchable[found + len(HOST_MARKER) :].lstrip()
        if not _reads_as_a_capacity(after):
            return found
        found = searchable.find(HOST_MARKER, found + 1)
    return -1


def _reads_as_a_capacity(text: str) -> bool:
    """Whether the text opens with a number and then a word for how much."""
    digits = 0
    while digits < len(text) and (text[digits].isdigit() or text[digits] in "-."):
        digits += 1
    if digits == 0 or not text[0].isdigit():
        return False
    following = text[digits:].lstrip("- ").split(" ")[0]
    word = following.strip(TITLE_PUNCTUATION).lower()
    # "6 Person" and "6 Persons" are the same claim, so the list is written
    # once in the singular and the plural is taken off here.
    return word.rstrip("s") in CAPACITY_WORDS or word in CAPACITY_WORDS


def _sold_as_a_fitting(searchable: str, rule: InterestRule) -> bool:
    """Whether an accessory word sits right beside one of the wanted words.

    Only beside, because the same word means opposite things at a distance: a
    table saw sold "with rolling stand" is a table saw, while a "guitar stand"
    is not a guitar. Adjacency is what tells those apart, so a bare list of
    words to reject would throw away the article along with the accessory.
    """
    return any(
        _sits_beside(searchable, term, noun)
        for term in rule.any_terms
        for noun in rule.accessory_nouns
    )


def _sits_beside(searchable: str, term: str, noun: str) -> bool:
    """Whether the noun is within a word or two of the term, on either side.

    Room for a word or two because a title rarely puts them flush together:
    "Guitar Hard Case" and "Guitar Tripod Holder" both describe a fitting, and
    neither says the two words back to back.
    """
    for found in standalone_mentions(searchable, term):
        starts = max(0, found - NEARBY_TEXT)
        ends = found + len(term) + NEARBY_TEXT
        before = _words_of(searchable[starts:found])[-ACCESSORY_WORD_GAP:]
        after = _words_of(searchable[found + len(term) : ends])[:ACCESSORY_WORD_GAP]
        if any(_is_the_word(word, noun) for word in (*before, *after)):
            return True
    return False


def _words_of(text: str) -> list[str]:
    """The words in a fragment, with punctuation read as a space.

    A title is not reliably spaced. "xdr monitor stand?Single Desk Mount"
    writes two words with nothing between them, and splitting on whitespace
    alone yields "stand?single" -- a word that is neither, and that no list of
    accessory nouns can ever match. So a monitor stand was reported as a
    monitor, and "Desk Mount" was pushed a word further away than it is.
    """
    spaced = "".join(" " if letter in TITLE_PUNCTUATION else letter for letter in text)
    return spaced.split()


def _is_the_word(word: str, noun: str) -> bool:
    """The noun itself or its plural, and not merely a word starting with it.

    Titles are full of punctuation and plurals -- "Hangers," is the noun, and
    "Mountain" is not -- so this compares whole words rather than prefixes.
    """
    bare = word.strip(TITLE_PUNCTUATION)
    return bare in (noun, f"{noun}s", f"{noun}es")


def _asks_too_much_of_retail(
    listing: Listing, total_cost: Decimal, ceiling: Decimal | None
) -> bool:
    """Whether the lot costs more of its stated retail than this rule allows.

    For a want that is only worth having as a steal. Some things are wanted at
    any fair price and some are wanted only when they are nearly free, and a
    rule had no way to say which it was: every price lever it owned was in
    dollars, and "cheap for what it is" is a proportion.

    Unlike the value floor above, a lot with no stated retail passes. The floor
    declines an unproven claim because it is asking whether the thing is worth
    enough to be real; this is only capping a claim that was actually made.
    """
    if ceiling is None or listing.estimated_retail is None:
        return False
    return total_cost > ceiling * listing.estimated_retail


def _worth_at_least(listing: Listing, floor: Decimal | None) -> bool:
    """Whether the lot is stated to be worth what the rule asked for.

    A floor nobody can check is not cleared: when a rule names one and the
    listing states no retail at all, the claim is unproven and the rule declines
    it rather than assuming in its favour.
    """
    if floor is None:
        return True
    retail = listing.estimated_retail
    return retail is not None and retail >= floor
