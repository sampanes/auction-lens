"""Whether a piece of text actually says a term, rather than merely containing it.

A bare ``in`` test reads "polished" as a shed and "washed" as a shed too, so a
rule asking for one gets bull bars and ice makers. The difference is where the
term starts and ends, which is a question about boundaries rather than about
which words a rule chose. Every term test in the project comes through here so
that a rule cannot mean one thing in scoring and another in logistics.

Configuration cannot say this for itself: the TOML reader strips a term, so a
term written with a leading space to force a word boundary silently becomes the
bare word again.
"""

from __future__ import annotations

from collections.abc import Iterator

# What a term is still the term with on the end. A rule asking for "shed" means
# "sheds" too, and nobody should have to write both.
PLURAL_ENDINGS = ("", "s", "es")


def mentions(text: str, term: str) -> bool:
    """Whether the text says the term as a whole word, plural included."""
    return first_mention(text, term) != -1


def first_mention(text: str, term: str) -> int:
    """Where the term first stands on its own, or -1 if it never does."""
    for start in standalone_mentions(text, term):
        return start
    return -1


def standalone_mentions(text: str, term: str) -> Iterator[int]:
    """Every position where the term stands on its own, earliest first."""
    found = text.find(term)
    while found != -1:
        if _stands_alone(text, found, found + len(term)):
            yield found
        found = text.find(term, found + 1)


def _stands_alone(text: str, start: int, end: int) -> bool:
    """Whether no other word runs into the term from either side."""
    return _nothing_before(text, start) and _nothing_after(text, end)


def _nothing_before(text: str, start: int) -> bool:
    """A word cannot begin in the middle of another one."""
    return start == 0 or not text[start - 1].isalnum()


def _nothing_after(text: str, end: int) -> bool:
    """A word may end or take a plural ending, but not run on into another."""
    return _trailing_letters(text, end) in PLURAL_ENDINGS


def _trailing_letters(text: str, end: int) -> str:
    """The unbroken run of letters and digits immediately after the term."""
    stop = end
    while stop < len(text) and text[stop].isalnum():
        stop += 1
    return text[end:stop]
