"""What is already known about a product, before any lot of it appears.

This is the one kind of evidence the program cannot gather for itself. It can
measure what a product fetches here, and that is a real signal -- but a price
says a crowd treats something as disposable without ever saying which part
breaks, or whether the thing is simply too small for the job. That knowledge
comes from people who owned one, it is researched once, and it does not change
between runs.

So it is configuration, written in advance and read at match time, never a
lookup while a report is being built: a note is attached because a title says
the product's name, which costs one literal phrase test. A run that cannot
reach the network still carries every note, because nothing is fetched.

Deliberately only words. A note explains a product to the person reading the
card and never moves a score, because the score has its own owners and a
sentence somebody typed is not evidence of the kind they weigh. The decision
stays the reader's, which is also the only honest place for it: owner feedback
describes a model in general, and the lot in front of them is one specific
used example of it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductNote:
    """One researched fact about a product, and the titles it belongs to.

    The two word lists are the pair the interest rules already use, and for
    the same reason. `any_terms` finds the product and has to stay broad
    enough to catch it however the warehouse worded the title, usually by the
    maker's name because that is the part they leave alone. `all_terms` is
    what makes a broad name safe: a maker sells more than the one product a
    note was written about, and a note keyed on the name alone lands on all
    of them. Requiring the thing itself beside the name costs nothing and
    removes them.

    A note with no `any_terms` matches nothing and is refused at load, because
    a note nobody can see is worse than no note -- it reads as covered.
    """

    any_terms: tuple[str, ...]
    note: str
    all_terms: tuple[str, ...] = ()
