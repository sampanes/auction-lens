"""The explicit map from a configured provider id to its site adapter.

Configuration may describe any provider, but provider pages are not generic.
Resolving that difference in one place prevents a valid-looking id from being
silently read with the wrong site's parser.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..config.provider import AcquisitionConfig, ProviderConfig
from .nellis.discover import check_discovery_ready as check_nellis_discovery_ready
from .nellis.discover import discover_searches as discover_nellis_searches
from .nellis.parse import read_saved_page as read_nellis_saved_page
from .nellis.parse import read_search_page as read_nellis_search_page


class SearchPageCapture(Protocol):
    """The provider-neutral facts collection needs about one saved search."""

    term: str
    url: str
    path: Path
    reused_cache: bool
    fetched_at: datetime


class Discovery(Protocol):
    def __call__(
        self,
        provider: ProviderConfig,
        config: AcquisitionConfig,
        terms: Iterable[str],
    ) -> Sequence[SearchPageCapture]: ...


class ReadinessCheck(Protocol):
    def __call__(
        self,
        provider: ProviderConfig,
        config: AcquisitionConfig,
        terms: Iterable[str],
    ) -> None: ...


class SearchPageReader(Protocol):
    def __call__(
        self, html: str, *, source: str, page_url: str
    ) -> list[dict[str, Any]]: ...


class SavedPageReader(Protocol):
    def __call__(
        self, html: str, *, source: str, page_url: str = ""
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ProviderAdapter:
    """Everything collection needs to understand one provider's pages."""

    discover_searches: Discovery
    check_discovery_ready: ReadinessCheck
    read_search_page: SearchPageReader
    read_saved_page: SavedPageReader


# Registration is deliberately written out. Adding a provider should be an
# obvious code change with four visible responsibilities, not import magic.
_PROVIDER_ADAPTERS: dict[str, ProviderAdapter] = {
    "nellis": ProviderAdapter(
        discover_searches=discover_nellis_searches,
        check_discovery_ready=check_nellis_discovery_ready,
        read_search_page=read_nellis_search_page,
        read_saved_page=read_nellis_saved_page,
    ),
}


def resolve_provider(provider_id: str) -> ProviderAdapter:
    """Return the exact adapter named by config, or fail before collection."""
    try:
        return _PROVIDER_ADAPTERS[provider_id]
    except KeyError:
        supported = ", ".join(sorted(_PROVIDER_ADAPTERS))
        raise ValueError(
            f"unsupported provider id {provider_id!r}; supported provider ids: {supported}"
        ) from None
