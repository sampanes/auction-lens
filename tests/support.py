"""Fixtures and fakes shared by the test modules.

Everything here is synthetic. The tests never contact a provider, a valuation
source, or an SMTP server.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from auction_lens.config import AppConfig, load_config
from auction_lens.ingest import load_listings
from auction_lens.models import Listing
from auction_lens.storage import Database

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = ROOT / "config" / "providers" / "nellis.example.toml"
SYNTHETIC_LISTINGS = ROOT / "fixtures" / "synthetic" / "listings.json"
NELLIS_BROWSE_FIXTURE = ROOT / "fixtures" / "nellis" / "browse-shell.html"

SOUNDBAR = 0  # A wanted listing that is also a retail-ratio anomaly.
LASER_LEVEL = 1  # An anomaly with no matching interest rule.


def example_config() -> AppConfig:
    return load_config(EXAMPLE_CONFIG)


def example_listings() -> list[Listing]:
    return load_listings(SYNTHETIC_LISTINGS)


@contextmanager
def temporary_directory() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


@contextmanager
def temporary_database() -> Iterator[Database]:
    """An initialized database in a directory that disappears afterwards."""
    with temporary_directory() as directory:
        database = Database.at(directory / "observations.sqlite3")
        database.initialize()
        yield database


class FakeResponse:
    """The small part of an HTTP response the fetchers actually use."""

    def __init__(self, body: bytes = b"", status: int = 200, headers: dict | None = None):
        self.body = body
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def read(self) -> bytes:
        return self.body


class RecordingOpener:
    """An opener that answers with a fixed response and remembers every call."""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[tuple] = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        return self.response

    @property
    def request_count(self) -> int:
        return len(self.calls)
