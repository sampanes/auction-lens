from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..config import ValuationSourceConfig
from ..models import Listing, ValuationObservation, money, parse_datetime
from .base import SourceResult


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _contains_phrase(haystack: str, needle: str) -> bool:
    return f" {needle} " in f" {haystack} "


class XmlCatalogAdapter:
    """Read human-reviewed comparable prices from a plain XML catalog."""

    def __init__(self, config: ValuationSourceConfig):
        self.config = config

    def collect(self, listing: Listing) -> SourceResult:
        path = Path(str(self.config.settings.get("path", "")))
        if not path.is_file():
            return SourceResult()
        root = ET.parse(path).getroot()
        observations: list[ValuationObservation] = []
        for entry in root.findall("entry"):
            if not self._matches(entry, listing):
                continue
            for price in entry.findall("price"):
                observations.append(self._observation(price))
        return SourceResult(observations=tuple(observations))

    def _matches(self, entry: ET.Element, listing: Listing) -> bool:
        category = _normalized(entry.get("category", ""))
        if category and listing.category and category != _normalized(listing.category):
            return False
        brand = _normalized(entry.get("brand", ""))
        model = _normalized(entry.get("model", ""))
        title = _normalized(" ".join((listing.brand, listing.model, listing.title)))
        if brand and not _contains_phrase(title, brand):
            return False
        if model and not _contains_phrase(title, model):
            return False
        terms = tuple(_normalized(value) for value in entry.get("terms", "").split("|") if value)
        return not terms or any(_contains_phrase(title, term) for term in terms)

    def _observation(self, price: ET.Element) -> ValuationObservation:
        typical = money(price.get("typical"), field_name="typical")
        low = money(price.get("low", typical), field_name="low")
        high = money(price.get("high", typical), field_name="high")
        if not low <= typical <= high:
            raise ValueError(
                f"valuation source {self.config.source_id!r} requires low <= typical <= high"
            )
        observed_at: datetime | None = parse_datetime(price.get("observed_at"))
        return ValuationObservation(
            source_id=self.config.source_id,
            basis=str(price.get("basis", "used_sold")).strip().lower(),
            low=low,
            typical=typical,
            high=high,
            currency=str(price.get("currency", "USD")).upper(),
            sample_size=max(1, int(price.get("sample_size", "1"))),
            confidence=Decimal(str(price.get("confidence", "1"))),
            observed_at=observed_at,
            url=str(price.get("url", "")),
            notes=str(price.get("notes", "")),
        )
