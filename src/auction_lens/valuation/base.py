"""The contract every valuation source implements.

It is intentionally one method: a source is asked about a listing and answers
with evidence, links, or neither. Everything else -- caching, authentication,
rate limits -- is the adapter's own business.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import Listing, ResearchLink, ValuationObservation


@dataclass(frozen=True)
class SourceResult:
    """What one source produced for one listing."""

    observations: tuple[ValuationObservation, ...] = ()
    research_links: tuple[ResearchLink, ...] = ()


class ValuationAdapter(Protocol):
    """Small contract implemented by every valuation input mechanism."""

    def collect(self, listing: Listing) -> SourceResult: ...
