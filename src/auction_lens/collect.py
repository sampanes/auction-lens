"""The commands that get lots out of a provider and into the canonical file.

Every command that contacts a listing provider comes through this workflow,
which is why it stays away from everything that scores or reports. Whatever it
writes, ``run`` can read without knowing where it came from, so a parser can be
corrected and re-run without asking the provider a second time.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config.app import AppConfig
from .config.load import load_config
from .files import read_json, write_json_atomically
from .listings.files import dated, unique_lots
from .providers.http import METADATA_SUFFIX, ResponseCache, fetch_authorized_page
from .providers.registry import ProviderAdapter, resolve_provider
from .providers.search_terms import search_terms

PAGE_SUFFIX = ".html"
LISTINGS_KEY = "listings"
NAMED_FAILED_PAGES = 5


@dataclass(frozen=True)
class DiscoveryStatus:
    """What discovery could not read, safe to carry into an unattended report."""

    failed_pages: tuple[str, ...] = ()

    @property
    def notices(self) -> tuple[str, ...]:
        """A bounded, reader-facing warning when the resulting file is incomplete."""
        if not self.failed_pages:
            return ()
        shown = ", ".join(self.failed_pages[:NAMED_FAILED_PAGES])
        remaining = len(self.failed_pages) - NAMED_FAILED_PAGES
        suffix = f", and {remaining} more" if remaining > 0 else ""
        return (
            f"Collection incomplete: {len(self.failed_pages)} provider page(s) "
            f"could not be read ({shown}{suffix}); this report may omit listings.",
        )


def fetch(args: argparse.Namespace) -> int:
    """Fetch one authorized page and report what happened to the cache."""
    config = load_config(args.config)
    result = fetch_authorized_page(config.provider, config.acquisition)
    provider = config.provider.display_name or config.provider.provider_id
    outcome = (
        "cached response reused"
        if result.reused_cache
        else f"{result.bytes_received} bytes cached"
    )
    print(f"{provider} returned HTTP {result.status}; {outcome} at {result.cache_path}")
    return 0


def discover(args: argparse.Namespace) -> int:
    """Ask the provider's search for lots, and write what it lists."""
    config = load_config(args.config)
    adapter = resolve_provider(config.provider.provider_id)
    run_discovery(args, config, search_terms(config, args.search), adapter=adapter)
    return 0


def run_discovery(
    args: argparse.Namespace,
    config: AppConfig,
    terms: list[str],
    *,
    adapter: ProviderAdapter,
) -> DiscoveryStatus:
    """Execute discovery and return anything an unattended report must disclose."""
    captures = adapter.discover_searches(config.provider, config.acquisition, terms)

    found = []
    failures: list[tuple[str, str]] = []
    for capture in captures:
        try:
            listed = adapter.read_search_page(
                capture.path.read_text(encoding="utf-8", errors="replace"),
                source=config.provider.provider_id,
                page_url=capture.url,
            )
        except ValueError as error:
            # One page the provider changed must not lose the other fifty, the
            # same way it does not in `pull`. This is the path that matters
            # most for it: an unattended run has already spent the requests by
            # the time a page fails to read, and throwing the rest away turns
            # one changed page into a silent evening.
            failures.append((capture.term, str(error)))
            continue
        # The page's own download time, not now: a page reused from the cache
        # describes prices that were true when it was fetched.
        found.extend(dated(listed, capture.fetched_at))
        state = "unchanged" if capture.reused_cache else "fetched"
        print(f"  {capture.term}: {len(listed)} lot(s) ({state})")

    _require_something_readable(captures, failures)
    rows = unique_lots(found)
    write_json_atomically(Path(args.output), {LISTINGS_KEY: rows})
    print(f"Found {len(rows)} lot(s) from {len(captures)} page(s) into {args.output}.")
    for term, error in failures:
        print(f"  [!] could not read {term}: {error}")
    return DiscoveryStatus(tuple(term for term, _error in failures))


def _require_something_readable(
    captures: Sequence[object], failures: list[tuple[str, str]]
) -> None:
    """Refuse a run in which every page failed, rather than reporting nothing.

    Losing some pages is worth continuing for; losing all of them is not, and
    the difference matters because the two look identical afterwards. An empty
    listings file reads exactly like a quiet day at the warehouse, and a quiet
    day is the one thing this program must never invent.
    """
    # Asked of the failures rather than the captures, because a run with no
    # pages at all has no failures either, and "none of none failed" is not
    # the provider changing anything.
    if failures and len(failures) == len(captures):
        raise ValueError(
            f"no page could be read; the provider may have changed. "
            f"First: {failures[0][0]}: {failures[0][1]}"
        )


def write_satisfied_discovery(output: str) -> None:
    """Complete a quiet daily run when every configured want is already filled."""
    write_json_atomically(Path(output), {LISTINGS_KEY: []})
    print("All finite interests are satisfied; no provider request was needed.")


def pull(args: argparse.Namespace) -> int:
    """Read saved provider pages into the canonical file that `run` analyses."""
    config = load_config(args.config)
    adapter = resolve_provider(config.provider.provider_id)
    pages = _saved_pages(Path(args.input))
    rows, failures = [], []
    for page in pages:
        try:
            listed = adapter.read_saved_page(
                page.read_text(encoding="utf-8", errors="replace"),
                source=config.provider.provider_id,
                page_url=_saved_page_url(page),
            )
            # A saved page can be read weeks after it was saved, so the lots in
            # it keep the date of the page rather than the date of this run.
            rows.extend(dated(listed, _saved_page_time(page)))
        except ValueError as error:
            # One page the provider changed must not lose the other fifty.
            failures.append(f"{page.name}: {error}")

    lots = unique_lots(rows)
    write_json_atomically(Path(args.output), {LISTINGS_KEY: lots})
    print(f"Read {len(lots)} lot(s) from {len(pages)} saved page(s) into {args.output}.")
    for failure in failures:
        print(f"  [!] {failure}")
    return 0


def _saved_pages(source: Path) -> list[Path]:
    """Accept one page or a directory of them, so a batch is not a special case."""
    if source.is_dir():
        return sorted(source.glob(f"*{PAGE_SUFFIX}"))
    if not source.is_file():
        raise ValueError(f"{source} is not a saved page or a directory of them")
    return [source]


def _saved_page_url(page: Path) -> str:
    """The address a saved page came from, recorded beside it when it was cached.

    A search page describes many lots and links each one relatively, so the
    address it was fetched from is what turns those links back into real ones.
    """
    metadata = read_json(page.with_suffix(page.suffix + METADATA_SUFFIX), default={})
    return str(metadata.get("source_url", ""))


def _saved_page_time(page: Path) -> datetime:
    """When a saved page was downloaded, which is when its prices were true.

    Pages saved before that was recorded have nothing better to offer than the
    file's own modification time, which is what writing it set.
    """
    recorded = ResponseCache.at(page).fetched_at()
    if recorded is not None:
        return recorded
    return datetime.fromtimestamp(page.stat().st_mtime, UTC)
