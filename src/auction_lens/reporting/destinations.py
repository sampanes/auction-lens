"""Opaque identities for places a report can be delivered.

The receipt ledger has to distinguish a new recipient from one that already
accepted a report, but an email address or webhook URL does not belong in that
machine-owned history. A full digest preserves that distinction without
copying the destination itself.
"""

from __future__ import annotations

from hashlib import sha256


def destination_fingerprint(value: str) -> str:
    """Identify one resolved destination without retaining its private value."""
    destination = value.strip()
    if not destination:
        raise ValueError("a delivery destination must be non-empty")
    return sha256(destination.encode("utf-8")).hexdigest()
