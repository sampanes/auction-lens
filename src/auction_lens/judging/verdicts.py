"""What a judge answers about one lot, and how its answer is read."""

from __future__ import annotations

import json
from dataclasses import dataclass

# What the judge may say to remove a lot. More than one word, because a
# model told to answer "discard" sometimes answers "remove", and refusing
# to understand a synonym would silently keep everything.
DISCARD_WORDS = frozenset({"discard", "remove", "reject", "drop", "no"})


@dataclass(frozen=True)
class Verdict:
    """One judgement about one lot, with the sentence that explains it.

    The explanation is not decoration. A term list that wrongly excludes
    something fails silently -- the lot simply never appears, and nobody can
    tell "nothing matched" from "something was wrongly rejected". A verdict
    that carries its reason turns that invisible failure into a readable one.
    """

    matches: bool
    why: str = ""

    @classmethod
    def kept(cls, why: str = "") -> Verdict:
        """A lot the judge accepts as the thing the interest asked for."""
        return cls(matches=True, why=why)

    @classmethod
    def dropped(cls, why: str) -> Verdict:
        """A lot the judge says is something else wearing the same word."""
        return cls(matches=False, why=why)


def verdict_from(answer: str) -> Verdict:
    """Read one reply, keeping the lot unless it was clearly asked to remove it.

    The judge is asked what to remove rather than what to keep, so a reply
    that says nothing, says something else, or cannot be parsed at all leaves
    the lot exactly where it was. The two mistakes are not equal: showing one
    extra baby monitor costs a line in an email, while hiding a real find
    costs the thing itself and does it silently.
    """
    try:
        decided = json.loads(answer)
    except (json.JSONDecodeError, TypeError):
        return Verdict.kept("the judge did not answer usably")
    if not isinstance(decided, dict):
        return Verdict.kept("the judge did not answer usably")
    why = str(decided.get("why", "")).strip()
    spoken = str(decided.get("verdict", "")).strip().lower()
    # Only the word that means "throw this away" removes anything. A missing
    # answer, an empty one, or a word nobody recognises all keep the lot.
    return Verdict.dropped(why) if spoken in DISCARD_WORDS else Verdict.kept(why)
