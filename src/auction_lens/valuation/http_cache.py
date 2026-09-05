"""Caching JSON valuation responses on disk.

Prices move slowly, so a cached answer from this morning is as good as a new
request and costs the source nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..file_io import write_bytes_atomically


@dataclass(frozen=True)
class JsonResponseCache:
    """One directory of cached responses for a single valuation source."""

    directory: Path
    source_id: str
    lifetime: timedelta
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def read_fresh(self, endpoint: str) -> Any | None:
        """Return the cached payload while it is still within its lifetime."""
        path = self.path_for(endpoint)
        if not path.is_file():
            return None
        written_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if self.clock() - written_at > self.lifetime:
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def store(self, endpoint: str, payload: Any) -> None:
        write_bytes_atomically(
            self.path_for(endpoint),
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def path_for(self, endpoint: str) -> Path:
        """Name the file after the source and a digest of the exact request."""
        digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        return self.directory / f"{self.source_id}-{digest}.json"
