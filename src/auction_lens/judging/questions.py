"""Turning one interest and one lot into a question a model can answer.

Kept apart from the model client because the wording is the part worth testing
and the part worth arguing about. Nothing here does any IO.
"""

from __future__ import annotations

from ..config import InterestRule
from ..models import Listing

# The judge is asked what to discard, and answers with a word rather than a
# flag. Both halves of that were measured on lots whose right answer was known
# by hand, because both are easy to get wrong in ways that look fine.
#
# The direction first. Asked "is this the thing?", a model that is unsure
# answers no and the lot disappears -- the same silent failure a wrong exclude
# term causes, which is what judging was added to end. Asked what to discard,
# an unsure model leaves the lot where a person can see it. A lot wrongly shown
# costs a line in an email; a lot wrongly hidden costs the thing itself.
#
# Then the answer's shape, which mattered more than expected. Asked for
# "remove": true or false, the model has to invert its own conclusion before
# writing it down, and on a full run it often did not: lots came back with
# remove=true beside a reason reading "Exact match." Scored over 23 known
# lots, that shape got 6 right. Asking for the word "keep" or "discard" got
# 20, and a word cannot be inverted by accident.
#
# Condition is deliberately not its business. Damage and missing parts are
# already scored against each interest's condition profile, and a judge that
# also refused on those terms would apply a second, unwritten policy that no
# configuration could see or change.
SCREENING_RULES = """You are screening auction lots for one buyer. A person
reads whatever you keep, so keeping something poor is cheap and discarding
something good is expensive.

Reply "discard" ONLY when you are confident the lot is not what the buyer
wants: it is an accessory or part sold on its own with none of the actual
thing included, or it is plainly a different product that merely shares a
word with what is wanted.

Reply "keep" in every other case, including when:
- the lot is a bundle, kit, set or system that contains the thing
- it comes with extras, or is sold together with something else
- its brand, model, size or material is unfamiliar to you
- you are unsure

Ignore condition entirely: damage, wear, missing parts and "for parts only"
never make it the wrong thing.

When in doubt, keep it. Answer with JSON only:
{"verdict": "keep" or "discard", "why": "<10 words or fewer>"}"""


def instructions_for(rule: InterestRule) -> str:
    """The standing brief, narrowed to what this one interest wants."""
    return f"{SCREENING_RULES}\n\nThe buyer wants: {rule.wants.strip()}"


def subject_of(listing: Listing) -> str:
    """The lot as the judge sees it: its title, and what the seller flagged.

    Condition notes are included even though condition is not the judge's
    business, because they are also where a lot admits to being an empty box,
    a manual, or a different product than the title suggests.
    """
    title = " ".join(listing.title.split())
    lines = [f"Lot title: {title}"]
    notes = " ".join((listing.notes or "").split())
    if notes:
        lines.append(f"Condition notes: {notes[:300]}")
    return "\n".join(lines)


def is_judgeable(rule: InterestRule) -> bool:
    """Whether this interest said, in words, what it is after.

    A rule with nothing written here is passed through untouched rather than
    judged against an empty sentence. Silence must not be read as "reject
    everything": an unwritten rule should behave exactly as it did before.

    One sentence, one kind of thing. A sentence that lists alternatives --
    "a microscope, a telescope, a spectrometer" -- is answered against the
    first item alone, and the others are removed for not being it. Where an
    interest genuinely covers several things, it wants several rules.
    """
    return bool(rule.wants.strip())
