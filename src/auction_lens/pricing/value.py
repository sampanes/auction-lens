"""Ask every relevant price source, then combine comparable evidence.

This is the whole pricing workflow. Source-specific fetching and parsing stay
in their own files; this module only decides which sources apply and how their
answers become one auditable summary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from importlib import import_module

from ..config.pricing import ValuationConfig, ValuationSourceConfig
from ..listings.model import Listing
from .http_json import HttpJsonAdapter
from .model import (
    ResearchLink,
    ValuationBand,
    ValuationObservation,
    ValuationSummary,
)
from .reference import ReferenceAdapter
from .sources import ValuationAdapter
from .xml_catalog import XmlCatalogAdapter

LOGGER = logging.getLogger(__name__)

# Sample size matters, but cannot let one enormous dataset silence every
# independent source. A source with no confidence still receives a tiny vote.
SAMPLE_SIZE_CAP = 25
MINIMUM_CONFIDENCE = Decimal("0.01")
BAND_EDGES = ("low", "typical", "high")
IMPORT_PATH_SEPARATOR = ":"

BUILTIN_ADAPTERS = {
    "reference": ReferenceAdapter,
    "xml_catalog": XmlCatalogAdapter,
    "http_json": HttpJsonAdapter,
}


def create_adapter(config: ValuationSourceConfig) -> ValuationAdapter:
    """Build a configured built-in or `package.module:Adapter` extension."""
    factory = BUILTIN_ADAPTERS.get(config.adapter)
    if factory is None:
        factory = _imported_factory(config.adapter)
    return factory(config)


def _imported_factory(adapter: str):
    """Load the deliberately small extension point named in public config."""
    if IMPORT_PATH_SEPARATOR not in adapter:
        choices = ", ".join(sorted(BUILTIN_ADAPTERS))
        raise ValueError(f"unknown valuation adapter {adapter!r}; built-ins: {choices}")
    module_name, attribute = adapter.split(IMPORT_PATH_SEPARATOR, 1)
    return getattr(import_module(module_name), attribute)


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
                LOGGER.warning(
                    "valuation source %s unavailable (%s)",
                    source.config.source_id,
                    type(error).__name__,
                )
                errors.append(
                    f"{source.config.source_id}: unavailable ({type(error).__name__})"
                )
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


def combine_into_bands(
    observations: list[ValuationObservation],
) -> tuple[ValuationBand, ...]:
    """Group like-for-like evidence and reduce each group to a single range."""
    bands = []
    for basis in sorted({item.basis for item in observations}):
        group = [item for item in observations if item.basis == basis]
        low, typical, high = (weighted_median(group, edge) for edge in BAND_EDGES)
        bands.append(
            ValuationBand(
                basis=basis,
                low=low,
                typical=typical,
                high=high,
                source_count=len({item.source_id for item in group}),
                sample_size=sum(item.sample_size for item in group),
            )
        )
    return tuple(bands)


def weighted_median(observations: list[ValuationObservation], edge: str) -> Decimal:
    """Return the value at which half of the group's total weight is reached."""
    weighted = sorted((getattr(item, edge), _weight_of(item)) for item in observations)
    halfway = sum(weight for _, weight in weighted) / 2
    reached = Decimal("0")
    for value, weight in weighted:
        reached += weight
        if reached >= halfway:
            return value
    return weighted[-1][0]


def _weight_of(observation: ValuationObservation) -> Decimal:
    confidence = max(MINIMUM_CONFIDENCE, observation.confidence)
    return confidence * Decimal(min(observation.sample_size, SAMPLE_SIZE_CAP))
