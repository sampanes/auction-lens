"""The commands that get lots out of a provider and into the canonical file.

These are the only commands that touch the network, which is why they are worth
keeping together and away from everything that scores or reports. Whatever they
write, ``run`` can read without knowing where it came from, so a parser can be
corrected and re-run without asking the provider a second time.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from ..acquisition import (
    METADATA_SUFFIX,
    ResponseCache,
    discover_searches,
    fetch_authorized_page,
)
from ..config import AppConfig, load_config
from ..file_io import read_json, write_json_atomically
from ..ingest import dated, read_saved_page, read_search_page, unique_lots
from .exit_codes import SUCCESS
from .searching import search_terms

PAGE_SUFFIX = ".html"
LISTINGS_KEY = "listings"


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
    return SUCCESS


def discover(args: argparse.Namespace) -> int:
    """Ask the provider's search for lots, and write what it lists."""
    config = load_config(args.config)
    return run_discovery(args, config, search_terms(config, args.search))


def run_discovery(args: argparse.Namespace, config: AppConfig, terms: list[str]) -> int:
    """Execute discovery with terms chosen by the calling workflow."""
    captures = discover_searches(config.provider, config.acquisition, terms)

    found = []
    for capture in captures:
        listed = read_search_page(
            capture.path.read_text(encoding="utf-8", errors="replace"),
            source=config.provider.provider_id,
            page_url=capture.url,
        )
        # The page's own download time, not now: a page reused from the cache
        # describes prices that were true when it was fetched.
        found.extend(dated(listed, capture.fetched_at))
        state = "unchanged" if capture.reused_cache else "fetched"
        print(f"  {capture.term}: {len(listed)} lot(s) ({state})")

    rows = unique_lots(found)
    write_json_atomically(Path(args.output), {LISTINGS_KEY: rows})
    print(f"Found {len(rows)} lot(s) from {len(captures)} page(s) into {args.output}.")
    return SUCCESS


def write_satisfied_discovery(output: str) -> None:
    """Complete a quiet daily run when every configured want is already filled."""
    write_json_atomically(Path(output), {LISTINGS_KEY: []})
    print("All finite interests are satisfied; no provider request was needed.")


def pull(args: argparse.Namespace) -> int:
    """Read saved provider pages into the canonical file that `run` analyses."""
    config = load_config(args.config)
    pages = _saved_pages(Path(args.input))
    rows, failures = [], []
    for page in pages:
        try:
            listed = read_saved_page(
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
    return SUCCESS


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
