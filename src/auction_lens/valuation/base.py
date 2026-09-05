from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import Listing, ResearchLink, ValuationObservation


@dataclass(frozen=True)
class SourceResult:
    observations: tuple[ValuationObservation, ...] = ()
    research_links: tuple[ResearchLink, ...] = ()


class ValuationAdapter(Protocol):
    """Small contract implemented by every valuation input mechanism."""

    def collect(self, listing: Listing) -> SourceResult: ...
