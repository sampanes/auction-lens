"""The last response received from a provider, plus how to revalidate it.

Keeping the validators next to the body is what lets a later run ask "has this
changed?" instead of downloading the same page again.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..file_io import read_json, write_bytes_atomically, write_json_atomically

METADATA_SUFFIX = ".metadata.json"

ETAG = "etag"
LAST_MODIFIED = "last_modified"


@dataclass(frozen=True)
class ResponseCache:
    """A cached response body and the metadata file beside it."""

    path: Path

    @classmethod
    def at(cls, path: str | Path) -> ResponseCache:
        return cls(Path(path))

    @property
    def metadata_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + METADATA_SUFFIX)

    def exists(self) -> bool:
        return self.path.exists()

    def size(self) -> int:
        return self.path.stat().st_size

    def conditional_headers(self) -> dict[str, str]:
        """Ask the provider to send a body only if the cached copy is stale."""
        metadata = read_json(self.metadata_path, default={})
        headers = {}
        if metadata.get(ETAG):
            headers["If-None-Match"] = metadata[ETAG]
        if metadata.get(LAST_MODIFIED):
            headers["If-Modified-Since"] = metadata[LAST_MODIFIED]
        return headers

    def store(
        self,
        body: bytes,
        *,
        headers: Mapping[str, str],
        fetched_at: datetime,
        source_url: str,
    ) -> None:
        """Replace the cached body, then record what could revalidate it."""
        write_bytes_atomically(self.path, body)
        write_json_atomically(
            self.metadata_path,
            {
                "fetched_at": fetched_at.isoformat(),
                ETAG: headers.get("ETag", ""),
                LAST_MODIFIED: headers.get("Last-Modified", ""),
                "source_url": source_url,
            },
        )
