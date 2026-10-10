"""Which slice of a long term list one sweep gets.

A configuration can name far more terms than a sweep is allowed to spend, and
the first version of this served the first window every time: a list of 30 had
12 of them searched every evening and the other 18 never asked about once,
while the sweep reported success. The fix is to remember where the last sweep
stopped, which turns a budget into a rota rather than a prefix.

A sweep is also not reliably allowed to spend the window it was given. Amazon
throttles one after a term or two and it abandons the attempt, so advancing by
the width of the window counts searches that never happened. That was not only
wasteful, it made half the list unreachable: a twelve-wide window on a list of
forty-six can only ever start at twenty-three of its forty-six positions,
because twelve and forty-six share a factor. The other twenty-three were
searched only when a sweep survived two terms, which is the thing that keeps
failing. `resume_after` is how a sweep that spent less than it was lent hands
the rest back.

The arithmetic lives here, apart from the file that stores the offset, because
a wrap-around is worth a test and a stored file is not.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple


class Rota(NamedTuple):
    """One sweep's share of the list, and where the next sweep should resume."""

    terms: list[str]
    start: int
    next_offset: int
    total: int

    def description(self) -> str:
        """Which part of the list this is, in words an operator can check.

        A window that wraps is two runs of the list and saying so is the point:
        reported as one run it reads "terms 25-36 of 30", which invites the
        reader to distrust the count rather than the sentence.
        """
        if not self.terms:
            return f"no terms this sweep, of {self.total}"
        last = self.start + len(self.terms)
        if last <= self.total:
            return f"terms {self.start + 1}-{last} of {self.total}"
        return (
            f"terms {self.start + 1}-{self.total} and 1-{last - self.total} "
            f"of {self.total}"
        )


def next_window(terms: Sequence[str], size: int, offset: int) -> Rota:
    """Take the next run of terms, wrapping around the end of the list.

    ``start`` is reported because an operator watching the console needs to know
    which part of their list this sweep is about. A sweep that says "12 terms"
    and means a different twelve each evening is the point; saying it without
    saying which twelve is just confusing.

    A budget wider than the list serves the list once rather than repeating
    terms within one sweep, and a budget of zero or less serves nothing: a
    negative one used to slice backwards and hand out a window twice the length
    of the list.
    """
    total = len(terms)
    if not total:
        return Rota([], 0, 0, 0)
    budget = max(size, 0)
    start = offset % total
    if budget >= total:
        # The whole list in one sweep is not a position in a rota, so report it
        # from the beginning rather than from wherever a narrower budget left
        # off. Otherwise the console announces terms 6-35 of 30.
        return Rota(list(terms), 0, 0, total)
    # Wrap rather than stop short, so a sweep is the full width it was allowed
    # even when the rota is sitting near the end of the list.
    doubled = list(terms) + list(terms)
    window = doubled[start : start + budget]
    return Rota(window, start, (start + len(window)) % total, total)


def resume_after(start: int, served: int, spent: int, total: int) -> int:
    """Where the rota should pick up after a sweep that spent `spent` searches.

    A sweep that searched every term it was served resumes past all of them. One
    cut short part-way hands back what it did not use, so the next sweep covers
    those terms instead of the list skipping them for a whole cycle.

    It always advances by at least one. A term that cannot be searched at all --
    a page that never renders, whatever the reason -- would otherwise be served
    again for ever, and the rota would stop being a rota. Stepping over it costs
    one term per attempt and keeps the list moving.
    """
    if total <= 0:
        return 0
    used = min(max(spent, 1), max(served, 1))
    return (start + used) % total
