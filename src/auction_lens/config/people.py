"""The household, so a clothing interest can say who it is shopping for.

An operator writes one ``[[people]]`` table per person and points several
interests at it by name, exactly as condition profiles work. This is the only
place that knows how a written size becomes the spelling matching compares.
"""

from __future__ import annotations

from ..matching.model import Person
from ..matching.sizes import STYLES
from ..matching.sizes import canonical_size as settle_size
from .toml import Section

FITS_KEY = "fits"


def read_people(root: Section) -> dict[str, Person]:
    """Every declared person, keyed by the name a rule writes in ``fits``."""
    people: dict[str, Person] = {}
    for item in root.tables("people"):
        person = _person(item)
        key = person.person_id.casefold()
        if key in people:
            raise ValueError(f"two people are both named {person.person_id!r}")
        people[key] = person
    return people


def resolve_person(owner: Section, people: dict[str, Person]) -> Person | None:
    """The person this interest is shopping for, or None if it does not care.

    Naming someone who was never declared is an error rather than a silent
    no-op: a typo that quietly disabled the size check would look exactly like
    a size check that passed, and the whole point is to stop the wrong size
    reaching the report.
    """
    name = owner.text(FITS_KEY).strip()
    if not name:
        return None
    person = people.get(name.casefold())
    if person is None:
        known = ", ".join(sorted(people)) or "none are declared"
        raise ValueError(f"unknown person {name!r} in fits; known people: {known}")
    return person


def _person(item: Section) -> Person:
    return Person(
        person_id=item.required_text("name"),
        sizes=frozenset(_canonical_size(size) for size in item.lowercase_texts("sizes")),
        styles=frozenset(_canonical_style(style) for style in item.lowercase_texts("styles")),
    )


def _canonical_size(written: str) -> str:
    """One spelling per size, so "Medium" and "M" in a config mean one thing.

    Refuse an unsupported spelling rather than quietly dropping it later. A
    typo that turns a person's declared chart into an empty chart would make
    every listing appear to fit, which is exactly the silent failure this
    feature exists to prevent.
    """
    canonical = settle_size(written)
    if not canonical:
        raise ValueError(
            f"unknown size {written!r}; use a letter size, numeric shoe size, "
            "or waist-by-inseam"
        )
    return canonical


def _canonical_style(written: str) -> str:
    """One spelling per audience, so "mens" and "men" are the same answer."""
    canonical = STYLES.get(written)
    if canonical is None:
        raise ValueError(f"unknown clothing style {written!r}")
    return canonical
