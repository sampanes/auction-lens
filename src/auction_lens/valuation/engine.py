"""Fanning one listing out to every configured valuation source."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..config import ValuationConfig, ValuationSourceConfig
from ..models import Listing, ResearchLink, ValuationObservation, ValuationSummary
from .aggregation import combine_into_bands
from .base import ValuationAdapter
from .registry import create_adapter


@dataclass(frozen=True)
class ConfiguredSource:
    """One source's settings paired with the adapter that will run it."""

    config: ValuationSourceConfig
    adapter: ValuationAdapter

    def applies_to(self, listing: Listing) -> bool:
        """A source with no categories is general; otherwise it must match."""
        return not self.config.categories or listing.category in self.config.categories


class ValuationEngine:
    """Ask every relevant source about a listing, then combine what they say."""

    def __init__(self, config: ValuationConfig):
        self.config = config
        self.sources = tuple(
            ConfiguredSource(source, create_adapter(source))
            for source in config.sources
            if source.enabled
        )

    def value(self, listing: Listing) -> ValuationSummary:
        observations: list[ValuationObservation] = []
        research_links: list[ResearchLink] = []
        errors: list[str] = []

        for source in self.sources:
            if not source.applies_to(listing):
                continue
            try:
                result = source.adapter.collect(listing)
            except Exception as error:
                # One optional source failing must not erase the other evidence.
                errors.append(f"{source.config.source_id}: {type(error).__name__}: {error}")
                continue
            observations.extend(self._in_configured_currency(result.observations, source))
            research_links.extend(result.research_links)

        return ValuationSummary(
            bands=combine_into_bands(observations),
            observations=tuple(observations),
            research_links=tuple(research_links),
            errors=tuple(errors),
        )

    def _in_configured_currency(
        self,
        observations: tuple[ValuationObservation, ...],
        source: ConfiguredSource,
    ) -> list[ValuationObservation]:
        """Drop other currencies, and fold the source's weight into confidence."""
        return [
            replace(item, confidence=item.confidence * source.config.weight)
            for item in observations
            if item.currency == self.config.currency
        ]
