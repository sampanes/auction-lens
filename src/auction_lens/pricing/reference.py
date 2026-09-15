"""A source that hands a person a place to look, without going there."""

from __future__ import annotations

from ..config.schema import ValuationSourceConfig
from ..listings.model import Listing
from .model import ResearchLink
from .sources import SourceResult, fill_template, settings_of


class ReferenceAdapter:
    """Generate a source-specific research link without contacting the site."""

    def __init__(self, config: ValuationSourceConfig):
        self.config = config
        self.settings = settings_of(config)

    def collect(self, listing: Listing) -> SourceResult:
        template = self.settings.required_text("url_template")
        return SourceResult(
            research_links=(
                ResearchLink(
                    source_id=self.config.source_id,
                    label=self.config.label or self.config.source_id,
                    url=fill_template(template, listing),
                ),
            )
        )
