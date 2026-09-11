"""Which configured want a lot answered, and how that want is getting on.

An interest is named in configuration, but a decision recorded last month has
to stay readable after that want is renamed, so what is stored is the stable id
with the name the person saw at the time.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..fields import require_at_least, require_not_negative


@dataclass(frozen=True)
class InterestRef:
    """A stable interest identity with the name a person saw at the time.

    The id answers which configured want this was. The name is kept beside it
    because an old decision should remain readable after that want is renamed.
    """

    interest_id: str
    name: str

    def __post_init__(self) -> None:
        interest_id = self.interest_id.strip()
        name = self.name.strip()
        if not interest_id:
            raise ValueError("interest id must be non-empty text")
        if not name:
            raise ValueError("interest name must be non-empty text")
        object.__setattr__(self, "interest_id", interest_id)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True)
class InterestProgress:
    """How a configured want stands against its explicit fulfillments."""

    interest: InterestRef
    wanted: int | None
    fulfilled: int = 0

    def __post_init__(self) -> None:
        if self.wanted is not None:
            require_at_least(self.wanted, 1, field_name="wanted")
        require_not_negative(self.fulfilled, field_name="fulfilled")

    @property
    def is_limited(self) -> bool:
        return self.wanted is not None

    @property
    def is_retired(self) -> bool:
        return self.wanted is not None and self.fulfilled >= self.wanted

    @property
    def remaining(self) -> int | None:
        if self.wanted is None:
            return None
        return max(self.wanted - self.fulfilled, 0)


def _unique_interest_refs(
    references: tuple[InterestRef, ...], *, field_name: str
) -> tuple[InterestRef, ...]:
    """Keep one reference per stable id and reject ambiguous direct callers."""
    unique = []
    seen = set()
    for reference in references:
        if not isinstance(reference, InterestRef):
            raise ValueError(f"{field_name} must contain interest references")
        key = reference.interest_id.casefold()
        if key in seen:
            raise ValueError(f"{field_name} contains duplicate id: {reference.interest_id}")
        seen.add(key)
        unique.append(reference)
    return tuple(unique)
