"""Getting listing data from a provider, kept separate from analyzing it."""

from .fetch import FetchResult, fetch_authorized_page

__all__ = ["FetchResult", "fetch_authorized_page"]
