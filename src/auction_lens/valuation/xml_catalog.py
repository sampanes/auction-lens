"""Comparable prices a person reviewed and wrote down.

XML is the format here because it survives hand editing and export from other
tools without whitespace or indentation changing what it means.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from decimal import Decimal
from pathlib import Path

from ..config import ValuationSourceConfig
from ..fields import parse_money, parse_utc_datetime
from ..models import Listing, ValuationObservation
from .base import SourceResult

TERM_SEPARATOR = "|"
WORD_PATTERN = re.compile(r"[a-z0-9]+")

DEFAULT_BASIS = "used_sold"
DEFAULT_CURRENCY = "USD"


class XmlCatalogAdapter:
    """Read human-reviewed comparable prices from a plain XML catalog."""

    def __init__(self, config: ValuationSourceConfig):
        self.config = config

    def collect(self, listing: Listing) -> SourceResult:
        path = Path(str(self.config.settings.get("path", "")))
        if not path.is_file():
            return SourceResult()
        catalog = ElementTree.parse(path).getroot()
        observations = [
            self._observation(price)
            for entry in catalog.findall("entry")
            if _entry_matches(entry, listing)
            for price in entry.findall("price")
        ]
        return SourceResult(observations=tuple(observations))

    def _observation(self, price: ElementTree.Element) -> ValuationObservation:
        typical = parse_money(price.get("typical"), field_name="typical")
        low = parse_money(price.get("low", typical), field_name="low")
        high = parse_money(price.get("high", typical), field_name="high")
        return ValuationObservation(
            source_id=self.config.source_id,
            basis=str(price.get("basis", DEFAULT_BASIS)).strip().lower(),
            low=low,
            typical=typical,
            high=high,
            currency=str(price.get("currency", DEFAULT_CURRENCY)).upper(),
            sample_size=int(price.get("sample_size", "1")),
            confidence=Decimal(str(price.get("confidence", "1"))),
            observed_at=parse_utc_datetime(price.get("observed_at")),
            url=str(price.get("url", "")),
            notes=str(price.get("notes", "")),
        )


def _entry_matches(entry: ElementTree.Element, listing: Listing) -> bool:
    """An entry applies when its category, brand, model, and terms all fit.

    Each attribute is optional, so a catalog can be as specific or as broad as
    the person maintaining it wants to be.
    """
    category = _normalized(entry.get("category", ""))
    if category and listing.category and category != _normalized(listing.category):
        return False

    described = _normalized(" ".join((listing.brand, listing.model, listing.title)))
    brand = _normalized(entry.get("brand", ""))
    model = _normalized(entry.get("model", ""))
    if brand and not _contains_phrase(described, brand):
        return False
    if model and not _contains_phrase(described, model):
        return False

    written = entry.get("terms", "").split(TERM_SEPARATOR)
    terms = [_normalized(value) for value in written if value]
    return not terms or any(_contains_phrase(described, term) for term in terms)


def _normalized(value: str) -> str:
    """Reduce text to lowercase words so punctuation cannot prevent a match."""
    return " ".join(WORD_PATTERN.findall(value.casefold()))


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Match whole words, so that "sb21" does not match "sb210"."""
    return f" {needle} " in f" {haystack} "
