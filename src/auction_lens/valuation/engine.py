from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from importlib import import_module

from ..config import ValuationConfig, ValuationSourceConfig
from ..models import Listing, ValuationBand, ValuationObservation, ValuationSummary
from .base import ValuationAdapter
from .http_json import HttpJsonAdapter
from .reference import ReferenceAdapter
from .xml_catalog import XmlCatalogAdapter


BUILTIN_ADAPTERS = {
    "reference": ReferenceAdapter,
    "xml_catalog": XmlCatalogAdapter,
    "http_json": HttpJsonAdapter,
}


def create_adapter(config: ValuationSourceConfig) -> ValuationAdapter:
    """Resolve a built-in adapter or an import path such as package.module:Adapter."""
    factory = BUILTIN_ADAPTERS.get(config.adapter)
    if factory is None:
        if ":" not in config.adapter:
            choices = ", ".join(sorted(BUILTIN_ADAPTERS))
            raise ValueError(f"unknown valuation adapter {config.adapter!r}; built-ins: {choices}")
        module_name, attribute = config.adapter.split(":", 1)
        factory = getattr(import_module(module_name), attribute)
    return factory(config)


class ValuationEngine:
    """Fan a listing out to configured sources, then combine like-for-like evidence."""

    def __init__(self, config: ValuationConfig):
        self.config = config
        self.sources = tuple(
            (source, create_adapter(source)) for source in config.sources if source.enabled
        )

    def value(self, listing: Listing) -> ValuationSummary:
        observations: list[ValuationObservation] = []
        links = []
        errors = []
        for source, adapter in self.sources:
            if source.categories and listing.category not in source.categories:
                continue
            try:
                result = adapter.collect(listing)
            except Exception as exc:  # A failed optional source must not erase other evidence.
                errors.append(f"{source.source_id}: {type(exc).__name__}: {exc}")
                continue
            observations.extend(
                replace(item, confidence=item.confidence * source.weight)
                for item in result.observations
                if item.currency == self.config.currency
            )
            links.extend(result.research_links)
        return ValuationSummary(
            bands=self._aggregate(observations),
            observations=tuple(observations),
            research_links=tuple(links),
            errors=tuple(errors),
        )

    @staticmethod
    def _aggregate(observations: list[ValuationObservation]) -> tuple[ValuationBand, ...]:
        bases = sorted({item.basis for item in observations})
        bands = []
        for basis in bases:
            group = [item for item in observations if item.basis == basis]
            bands.append(
                ValuationBand(
                    basis=basis,
                    low=_weighted_median(group, "low"),
                    typical=_weighted_median(group, "typical"),
                    high=_weighted_median(group, "high"),
                    source_count=len({item.source_id for item in group}),
                    sample_size=sum(item.sample_size for item in group),
                )
            )
        return tuple(bands)


def _weighted_median(observations: list[ValuationObservation], field: str) -> Decimal:
    weighted = sorted(
        (
            getattr(item, field),
            max(Decimal("0.01"), item.confidence) * Decimal(min(item.sample_size, 25)),
        )
        for item in observations
    )
    threshold = sum(weight for _, weight in weighted) / 2
    cumulative = Decimal("0")
    for value, weight in weighted:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return weighted[-1][0]
