"""Matching a listing against the explicit reasons an operator wants things.

An interest says why an item would be useful, so a match starts from a high
score and is reduced only by conditions that the stated purpose cares about.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import InterestRule
from ..models import BASE_INTEREST_SCORE, Candidate, CandidateCategory, Listing
from ..text_match import first_mention, mentions, standalone_mentions
from .conditions import penalty_for, policy_admits
from .context import ScoringContext
from .signals import clamp_score

# The word an accessory uses to name what it fits. Spaced so that it is the
# whole word: "for" and not the tail of "comfort".
HOST_MARKER = " for "

# How far from the wanted word an accessory word still counts as attached to it.
ACCESSORY_WORD_GAP = 2

# Titles end words with these; they are not part of the word.
TITLE_PUNCTUATION = ".,:;!?()[]{}\"'-*/"


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
    # Against the wider text: a seller's note about this one item is exactly
    # the kind of thing that should be able to rule it out.
    if any(mentions(listing.disqualifying_text, term) for term in rule.exclude_terms):
        return False
    if describes_an_accessory(searchable, rule):
        return False
    if not _worth_at_least(listing, rule.minimum_retail):
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
    host_marker = searchable.find(HOST_MARKER)
    if host_marker == -1:
        return False
    # A rule that named no wanted words has nothing here to be positioned, and
    # an empty "all of them are late" would otherwise be vacuously true.
    named = [first_mention(searchable, term) for term in rule.any_terms]
    said = [at for at in named if at != -1]
    return bool(said) and all(at > host_marker for at in said)


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
        before = searchable[:found].split()[-ACCESSORY_WORD_GAP:]
        after = searchable[found + len(term) :].split()[:ACCESSORY_WORD_GAP]
        if any(_is_the_word(word, noun) for word in (*before, *after)):
            return True
    return False


def _is_the_word(word: str, noun: str) -> bool:
    """The noun itself or its plural, and not merely a word starting with it.

    Titles are full of punctuation and plurals -- "Hangers," is the noun, and
    "Mountain" is not -- so this compares whole words rather than prefixes.
    """
    bare = word.strip(TITLE_PUNCTUATION)
    return bare in (noun, f"{noun}s", f"{noun}es")


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
