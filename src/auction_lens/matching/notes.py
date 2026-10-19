"""Decide which researched product notes a listing carries.

The whole of this is a literal phrase test, and it lives beside the other term
matching for one reason: a note has to match the way a rule's words match, or
the two would disagree about what a title says. `mentions` is reused rather
than reimplemented, so a note term is a whole phrase here exactly as it is
there -- which is what keeps a note about one maker off a lot that merely
spells part of its name inside a longer word.

Notes do not compete and are not ranked. Every one whose terms the title says
is carried, in the order they were configured, because two true things about a
product are both worth knowing and the config is the only place that can say
which matters more.
"""

from __future__ import annotations

from ..config.notes import ProductNote
from ..listings.model import Listing
from .text import mentions


def notes_for(
    listing: Listing, notes: tuple[ProductNote, ...]
) -> tuple[str, ...]:
    """The note text of every configured note this listing's title names.

    Reads the shared searchable text rather than the raw title, so a note is
    found under the same normalization a rule's terms are found under.
    """
    searchable = listing.searchable_text
    return tuple(
        note.note
        for note in notes
        if any(mentions(searchable, term) for term in note.terms)
    )
