from __future__ import annotations

from urllib.parse import quote_plus

from ..config import ValuationSourceConfig
from ..models import Listing, ResearchLink
from .base import SourceResult


class ReferenceAdapter:
    """Generate a source-specific research link without scraping the destination."""

    def __init__(self, config: ValuationSourceConfig):
        self.config = config

    def collect(self, listing: Listing) -> SourceResult:
        template = str(self.config.settings.get("url_template", "")).strip()
        if not template:
            raise ValueError(f"valuation source {self.config.source_id!r} needs url_template")
        query = " ".join(value for value in (listing.brand, listing.model) if value) or listing.title
        replacements = {
            "query": quote_plus(query),
            "brand": quote_plus(listing.brand),
            "model": quote_plus(listing.model),
            "category": quote_plus(listing.category),
        }
        url = template
        for name, value in replacements.items():
            url = url.replace("{" + name + "}", value)
        return SourceResult(
            research_links=(
                ResearchLink(
                    source_id=self.config.source_id,
                    label=self.config.label or self.config.source_id,
                    url=url,
                ),
            )
        )
